import json
import math
import os
import re

import ifcopenshell
import ifcopenshell.util.placement
import pandas as pd
import pint
import pyproj
from flask import (
    Flask,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from pyproj import Transformer
from scipy.optimize import leastsq
from werkzeug.utils import secure_filename

import georeference_ifc


app = Flask(__name__, static_url_path='/static', static_folder='static')

# Secrets / runtime config from environment (see .env.example).
# FLASK_SECRET_KEY signs the session cookie — set a strong random value in
# production. The dev fallback is OK for local development only.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-me')
MAPTILER_KEY = os.environ.get('MAPTILER_KEY', '')

app.config['UPLOAD_FOLDER'] = 'uploads'
MAX_UPLOAD_MB = 50
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_EXTENSIONS = {'ifc'}  # Define allowed file extensions as a set

@app.errorhandler(413)
def _too_large(_e):
    msg = (
        f"File too large — the upload limit is {MAX_UPLOAD_MB} MB. "
        "Trim your IFC to a site/coordination model (terrain, site boundary, "
        "georeferencing data only) before uploading. Federated multi-discipline "
        "models often expose sensitive data — keep them on your device."
    )
    return render_template('upload.html', error_message=msg), 413

# Function to check if a filename has an allowed extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def purge_uploads():
    """Wipe every file in the upload cache — called on each new upload."""
    folder = app.config['UPLOAD_FOLDER']
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

def georef(ifc_file):
    geo = False
    #check ifc version
    version = ifc_file.schema
    message = f"IFC version: {version}\n"
    # Check the file is georefed or not
    mapconversion = None
    crs = None

    if ifc_file.schema[:4] == 'IFC4':
        project = ifc_file.by_type("IfcProject")[0]
        for c in (m for c in project.RepresentationContexts for m in c.HasCoordinateOperation):
            mapconversion = c
            crs = c.TargetCRS
        if mapconversion is not None:
            message += "IFC file is georeferenced.\n"
            geo = True
    if ifc_file.schema == 'IFC2X3':
        site = ifc_file.by_type("IfcSite")[0]
        psets = ifcopenshell.util.element.get_psets(site)
        if 'ePSet_MapConversion' in psets.keys() and 'ePSet_ProjectedCRS' in psets.keys():
            message += "IFC file is georeferenced.\n"
            geo = True
    return message , geo
        
def infoExt(filename , epsgCode):
    ureg = pint.UnitRegistry()
    ifc_file = fileOpener(filename)
    #check ifc version
    version = ifc_file.schema
    messages = [('IFC version', version)]
    ifc_site = ifc_file.by_type("IfcSite")


    #Find Longtitude and Latitude
    RLat = ifc_site[0].RefLatitude
    RLon = ifc_site[0].RefLongitude
    RElev = ifc_site[0].RefElevation
    if RLat is not None and RLon is not None:
        x0= (float(RLat[0]) + float(RLat[1])/60 + float(RLat[2]+RLat[3]/1000000)/(60*60))
        y0= (float(RLon[0]) + float(RLon[1])/60 + float(RLon[2]+RLon[3]/1000000)/(60*60))
        session['Refl'] = True
        if hasattr(ifc_file.by_type("IfcSite")[0], "ObjectPlacement") and ifc_file.by_type("IfcSite")[0].ObjectPlacement.is_a("IfcLocalPlacement"):
            local_placement = ifc_file.by_type("IfcSite")[0].ObjectPlacement.RelativePlacement
                # Check if the local placement is an IfcAxis2Placement3D
            if local_placement.is_a("IfcAxis2Placement3D"):
                local_origin = local_placement.Location.Coordinates
                bx,by,bz= local_origin
                messages.append(('IFC Local Origin', local_origin))
            else:
                    errorMessage = "Local placement is not IfcAxis2Placement3D."
                    return messages, errorMessage
        else:
                errorMessage = "IfcSite does not have a local placement."
                return messages, errorMessage
    else:
        session['Refl'] = False
        messages.append(('RefLatitude or RefLongitude', 'Not available'))
    Refl = session.get('Refl')
    crs = None
    if ifc_file.schema[:4] != 'IFC4' and ifc_file.schema != 'IFC2X3':
        errorMessage = "IFC2X3, IFC4, and newer versions are supported.\n"
        return messages, errorMessage

    # Find local origin

                
    # Target CRS unit name
    try: 
        crs = pyproj.CRS.from_epsg(int(epsgCode))
    except:
        errorMessage = "CRS is not available."
        return messages, errorMessage


    crsunit = crs.axis_info[0].unit_name

    if crs.is_projected:
        messages.append(('Target CRS Type', 'Projected'))
        messages.append(('Target CRS EPSG', epsgCode))

    else:
        errorMessage = "CRS is not projected (geographic)."
        return messages, errorMessage
    target_epsg = "EPSG:"+str(epsgCode)
    transformer = Transformer.from_crs("EPSG:4326", target_epsg, always_xy=True)
    lon0, lat0 = y0, x0
    x1,y1,z1 = transformer.transform(lon0, lat0, RElev)
    # IFC length unit name
    ifc_units = ifc_file.by_type("IfcUnitAssignment")[0].Units
    for ifc_unit in ifc_units:
        if ifc_unit.is_a("IfcSIUnit") and ifc_unit.UnitType == "LENGTHUNIT":
            if ifc_unit.Prefix is not None:
                ifcunit = ifc_unit.Prefix + ifc_unit.Name
            else:
                ifcunit = ifc_unit.Name
    try:
        quantity = unitmapper(ifcunit)
        ifcmeter = quantity.to(ureg.meter).magnitude
    except:
        ifcmeter = None
    try:
        quantity = unitmapper(crsunit)
        crsmeter = quantity.to(ureg.meter).magnitude
    except:
        crsmeter = None

    if crsmeter is not None and ifcmeter is not None:
        coeff= ifcmeter/crsmeter
    else:
        errorMessage = "IFC/Map unit error"
        return messages, errorMessage
    if Refl:
        messages.append(("Reference Longitude",y0))
        messages.append(("Reference Latitude",x0))
        messages.append(("Reference Elevation",RElev))

    messages.append(("Target CRS Unit",str.lower(crsunit)))

    session['mapunit'] = str.lower(crsunit)

    if ifcunit:
        unit_name = ifcunit
        messages.append(("IFC Unit",str.lower(unit_name)))
        session['ifcunit'] = str.lower(unit_name)

    else:
        errorMessage = "No length unit found in the IFC file."
        return messages, errorMessage
    messages.append(("Unit Conversion Ratio",coeff))
    errorMessage = ""
    session['coeff'] = coeff
    if Refl:
        x1,y1,z1 = transformer.transform(lon0, lat0, RElev)
        x2= x1*coeff
        y2= y1*coeff
        session['xt'] = x1
        session['yt'] = y1
        session['zt'] = z1

    return messages, errorMessage

def _ifc_length_to_metre(ifc_file):
    """Return the factor that converts the IFC project's length unit to metres.
    Falls back to 1.0 if it cannot be determined."""
    prefix_map = {
        None: 1.0,
        'EXA': 1e18, 'PETA': 1e15, 'TERA': 1e12, 'GIGA': 1e9,
        'MEGA': 1e6, 'KILO': 1e3, 'HECTO': 1e2, 'DECA': 1e1,
        'DECI': 1e-1, 'CENTI': 1e-2, 'MILLI': 1e-3,
        'MICRO': 1e-6, 'NANO': 1e-9, 'PICO': 1e-12,
        'FEMTO': 1e-15, 'ATTO': 1e-18,
    }
    nonsi_map = {'INCH': 0.0254, 'FOOT': 0.3048, 'YARD': 0.9144, 'MILE': 1609.344}
    try:
        ua = ifc_file.by_type('IfcUnitAssignment')[0]
    except (IndexError, Exception):
        return 1.0
    for u in ua.Units:
        try:
            if u.is_a('IfcSIUnit') and u.UnitType == 'LENGTHUNIT':
                return prefix_map.get(u.Prefix, 1.0)
            if u.is_a('IfcConversionBasedUnit') and u.UnitType == 'LENGTHUNIT':
                name = (u.Name or '').upper()
                if name in nonsi_map:
                    return nonsi_map[name]
                try:
                    return float(u.ConversionFactor.ValueComponent.wrappedValue)
                except Exception:
                    pass
        except Exception:
            continue
    return 1.0


def check_corenet_sg(ifc_file):
    """
    Corenet X IFC-SG geo-referencing compliance check.

    Corenet X expects IfcSite.ObjectPlacement to carry the real-world SVY21
    (EPSG:3414) Eastings / Northings / Elevation — NOT IfcMapConversion.
    Returns a dict: { status: 'pass'|'fail'|'na', is_sg: bool, checks: [...] }.
    """
    result = {'status': 'na', 'is_sg': False, 'checks': []}
    if ifc_file is None:
        return result

    try:
        site = ifc_file.by_type('IfcSite')[0]
    except (IndexError, Exception):
        return result

    # Placements come back in the IFC project's length unit. Convert to metres
    # before comparing against the SVY21 Singapore range.
    to_m = _ifc_length_to_metre(ifc_file)

    # IfcSite.ObjectPlacement → Eastings (X), Northings (Y), Elevation (Z)
    site_x = site_y = site_z = None
    try:
        op = site.ObjectPlacement
        if op and op.is_a('IfcLocalPlacement'):
            rel = op.RelativePlacement
            if rel and rel.is_a('IfcAxis2Placement3D'):
                cx, cy, cz = rel.Location.Coordinates
                site_x, site_y, site_z = float(cx), float(cy), float(cz)
    except Exception:
        pass

    # Reference lat / lon (optional — used only for SG detection)
    ref_lat = ref_lon = None
    try:
        if site.RefLatitude:
            d, m, s = site.RefLatitude[0], site.RefLatitude[1], site.RefLatitude[2]
            ref_lat = float(d) + float(m) / 60 + float(s) / 3600
        if site.RefLongitude:
            d, m, s = site.RefLongitude[0], site.RefLongitude[1], site.RefLongitude[2]
            ref_lon = float(d) + float(m) / 60 + float(s) / 3600
    except Exception:
        pass

    # Is this a Singapore model?
    crs_is_svy21 = False
    try:
        for crs in ifc_file.by_type('IfcProjectedCRS'):
            name = (crs.Name or '').upper()
            if 'SVY21' in name or '3414' in name:
                crs_is_svy21 = True
                break
    except Exception:
        pass

    SG_E_MIN, SG_E_MAX = 0, 56000
    SG_N_MIN, SG_N_MAX = 15000, 60000

    # IFC-SG site boundary — IfcGeographicElement with ObjectType='SITEBOUNDARY'.
    # Its absolute placement (via IfcLocalPlacement chain) should land in the
    # SVY21 Singapore range to count as Corenet X IFC-SG geo-referenced.
    # Coords are converted to metres via `to_m` before bounds-checking.
    siteboundary_found = False
    siteboundary_xy = None  # in metres
    siteboundary_in_svy21 = False
    try:
        for ge in ifc_file.by_type('IfcGeographicElement'):
            otype = (getattr(ge, 'ObjectType', '') or '').upper()
            if otype != 'SITEBOUNDARY':
                continue
            siteboundary_found = True
            try:
                m = ifcopenshell.util.placement.get_local_placement(ge.ObjectPlacement)
                bx = float(m[0][3]) * to_m
                by = float(m[1][3]) * to_m
                siteboundary_xy = (bx, by)
                if SG_E_MIN <= bx <= SG_E_MAX and SG_N_MIN <= by <= SG_N_MAX:
                    siteboundary_in_svy21 = True
            except Exception:
                pass
            break
    except Exception:
        pass

    # IfcSite placement coords in metres
    site_x_m = site_x * to_m if site_x is not None else None
    site_y_m = site_y * to_m if site_y is not None else None
    site_z_m = site_z * to_m if site_z is not None else None

    lat_in_sg = ref_lat is not None and 1.1 <= ref_lat <= 1.55
    lon_in_sg = ref_lon is not None and 103.5 <= ref_lon <= 104.1
    site_in_svy21_sg = (
        site_x_m is not None and site_y_m is not None
        and SG_E_MIN <= site_x_m <= SG_E_MAX
        and SG_N_MIN <= site_y_m <= SG_N_MAX
    )
    is_sg = (
        (lat_in_sg and lon_in_sg)
        or site_in_svy21_sg
        or crs_is_svy21
        or siteboundary_in_svy21
    )
    result['is_sg'] = is_sg
    if not is_sg:
        return result

    east_in_range = site_x_m is not None and SG_E_MIN <= site_x_m <= SG_E_MAX
    north_in_range = site_y_m is not None and SG_N_MIN <= site_y_m <= SG_N_MAX
    site_in_range = bool(east_in_range and north_in_range)
    coords_in_svy21 = site_in_range or siteboundary_in_svy21

    # Pick a representative coordinate to display for check 3 — prefer IfcSite
    # if it's in range, otherwise the site boundary, otherwise whatever's set.
    # All values shown in metres (converted from the IFC project length unit).
    if site_in_range and site_x_m is not None:
        coord_value = f'IfcSite E {site_x_m:.3f} m, N {site_y_m:.3f} m'
    elif siteboundary_in_svy21 and siteboundary_xy is not None:
        bx, by = siteboundary_xy
        coord_value = f'SITEBOUNDARY E {bx:.3f} m, N {by:.3f} m'
    elif site_x_m is not None and (site_x_m != 0 or site_y_m != 0):
        coord_value = f'IfcSite E {site_x_m:.3f} m, N {site_y_m:.3f} m (outside SG)'
    elif siteboundary_xy is not None:
        bx, by = siteboundary_xy
        coord_value = f'SITEBOUNDARY E {bx:.3f} m, N {by:.3f} m (outside SG)'
    else:
        coord_value = 'Model at origin'

    checks = [
        {
            'label': 'CRS is SVY21 (EPSG:3414)',
            'pass': bool(crs_is_svy21),
            'value': 'SVY21 / EPSG:3414' if crs_is_svy21 else 'Not declared',
            'tip': 'Set the project coordinate reference system to SVY21 (EPSG:3414) in your BIM software, then re-export.',
        },
        {
            'label': 'IfcGeographicElement with ObjectType = SITEBOUNDARY exists',
            'pass': bool(siteboundary_found),
            'value': 'Found' if siteboundary_found else 'Missing',
            'tip': 'Ensure the site boundary element is exported. Re-check your IFC export mapping (the site boundary must be exported as IfcGeographicElement with ObjectType=SITEBOUNDARY) and verify in an IFC viewer.',
        },
        {
            'label': 'Coordinates land within the SVY21 Singapore map',
            'pass': bool(coords_in_svy21),
            'value': coord_value,
            'tip': 'Coordinates fall outside the SVY21 Singapore range. Re-check your IFC export settings — in Revit, enable Shared Coordinates so real-world Eastings/Northings carry through to IfcSite.ObjectPlacement.',
        },
    ]
    result['checks'] = checks
    result['status'] = 'pass' if all(c['pass'] for c in checks) else 'fail'
    return result


def unitmapper(value):
    ureg = pint.UnitRegistry()
    unit_mapping = {
    "METRE": ureg.meter,
    "METER": ureg.meter,
    "CENTIMETRE": ureg.centimeter,
    "CENTIMETER": ureg.centimeter,
    "MILLIMETRE": ureg.millimeter,
    "MILLIMETER": ureg.millimeter,
    "INCH": ureg.inch,
    "FOOT": ureg.foot,
    "YARD": ureg.yard,
    "MILE": ureg.mile,
    "NAUTICAL_MILE": ureg.nautical_mile,
    "metre": ureg.meter,
    "meter": ureg.meter,
    "centimeter": ureg.centimeter,
    "centimetre": ureg.centimeter,
    "millimeter": ureg.millimeter,
    "millimetre": ureg.millimeter,
    "inch": ureg.inch,
    "foot": ureg.foot,
    "yard": ureg.yard,
    "mile": ureg.mile,
    "nautical_mile": ureg.nautical_mile,
    # Add more mappings as needed
    }
    if value in unit_mapping:
            return  1 * unit_mapping[value]
    return

@app.route('/')
def index():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    purge_uploads()
    if 'file' not in request.files:
        return "No file part"
    file = request.files['file']
    if file.filename == '':
        return "No selected file"
    if file and allowed_file(file.filename):  # Check if the file extension is allowed
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        ifc_file = fileOpener(filename)

        message, geo = georef(ifc_file)
        if geo:
            IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
            df = pd.DataFrame(list(IfcProjectedCRS.__dict__.items()), columns= ['property', 'value'])
            dg = pd.DataFrame(list(IfcMapConversion.__dict__.items()), columns= ['property', 'value'])
            html_table_f = df.to_html()
            html_table_g = dg.to_html()
            compliance = check_corenet_sg(ifc_file)
            IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
            target = IfcProjectedCRS.Name.split(':')
            epsg = int(target[1])
            message2 = infoExt(filename,epsg)
            coeff = session.get('coeff')
            if coeff is None:
                return render_template('result.html', filename=filename, table_f=html_table_f, table_g=html_table_g, message=message2, compliance=compliance)

            if int(coeff)!=1 and IfcMapConversion.Scale is None:
                message += "There is a conflict between Scale factor and unit conversion. (Yet to be decided by buildingSmart.)"
                session['scaleError']=True
                return render_template('result.html', filename=filename, table_f=html_table_f, table_g=html_table_g, message=message, compliance=compliance)
            if int(coeff)!=1 and int(IfcMapConversion.Scale) == 1:
                message += "There is a conflict between Scale factor and unit conversion. (Yet to be decided by buildingSmart.)"
                session['scaleError']=True
            return render_template('result.html', filename=filename, table_f=html_table_f, table_g=html_table_g, message=message, compliance=compliance)
        
        return redirect(url_for('convert_crs', filename=filename))  # Redirect to EPSG code input page
    else:
        return render_template('upload.html', error_message="Invalid file format. Please upload a .ifc file.")

@app.route('/convert/<filename>', methods=['GET', 'POST'])
def convert_crs(filename):
    if request.method == 'POST':
        try:
            epsg_code = int(request.form['epsg_code'])
        except ValueError:
            message = "Invalid EPSG code. Please enter a valid integer."
            return render_template('convert.html', filename=filename, message=message)
        session['target_epsg'] = epsg_code
       # Call the infoExt function and unpack the results
        messages, error = infoExt(filename, epsg_code)
        if error == "":
            # Pass x2, y2, and z1 to the survey_points route
            return redirect(url_for('survey_points', filename=filename))
        return render_template('convert.html', filename=filename, message=error)

    return render_template('convert.html', filename=filename)

@app.route('/survey/<filename>', methods=['GET', 'POST'])
def survey_points(filename):
    epsg_code = session.get('target_epsg')
    messages, error = infoExt(filename, epsg_code)
    ifcunit = session.get('ifcunit')
    mapunit = session.get('mapunit')
    Refl = session.get('Refl')
    if request.method == 'POST':
        box_number = request.form.get('boxNumber')
        if box_number == '3':
            session['Refl'] = False
            Refl = False
    if Refl:
        messages , error = local_trans(filename,messages)
        Num = []
        if request.method == 'POST':
            try:
                Num = int(request.form['Num'])
                if Num < 0:
                    error += "Please enter zero or a positive integer."
                    return render_template('survey.html', filename=filename, messages=messages, error=error)
            except ValueError:
                error += "Please enter zero or a positive integer."
                return render_template('survey.html', filename=filename, messages=messages, error=error)
            session['rows'] = Num
            if Num == 0:
                return redirect(url_for('calculate', filename=filename))
        return render_template('survey.html', filename=filename, messages=messages, Num=Num, ifcunit=ifcunit, mapunit=mapunit, error=error, Refl = Refl)
    else:
        error += '\nThe model has no surveyed or georeferenced attribute.\nYou need to provide at least one point in local and target CRS.'
        error += '\n\nAccuracy of the results improves as you provide more georeferenced points.\nWithout any additional georeferenced points, it is assumed that the model is scaled based on unit conversion and rotation is derived from TrueNorth direction (if availalble).\n'
        Num = []
        if request.method == 'POST':
            try:
                Num = int(request.form['Num'])
                if Num <= 0:
                    error += "Please enter a positive integer."
                    return render_template('survey.html', filename=filename, error=error)
            except ValueError:
                error += "Please enter a positive integer."
                return render_template('survey.html', filename=filename, error=error)
            session['rows'] = Num
        return render_template('survey.html', filename=filename, messages=messages, Num=Num, ifcunit=ifcunit, mapunit=mapunit, Refl = Refl)


def local_trans(filename , messages):
    ifc_file = fileOpener(filename)
    xt = session.get('xt')
    yt = session.get('yt')
    zt = session.get('zt')
    bx,by,bz = 0,0,0
    error = ""
    if hasattr(ifc_file.by_type("IfcSite")[0], "ObjectPlacement") and ifc_file.by_type("IfcSite")[0].ObjectPlacement.is_a("IfcLocalPlacement"):
        local_placement = ifc_file.by_type("IfcSite")[0].ObjectPlacement.RelativePlacement
        # Check if the local placement is an IfcAxis2Placement3D
        if local_placement.is_a("IfcAxis2Placement3D"):
            local_origin = local_placement.Location.Coordinates
            bx, by, bz = map(float, local_origin)
            messages.append(("First Point Local Coordinates",str(local_origin)))
        else:
                error += "Local placement is not IfcAxis2Placement3D."
    else:
            error += "IfcSite does not have a local placement."
    session['bx'] = bx
    session['by'] = by        
    session['bz'] = bz        

    messages.append(("First Point Target coordinates" , ("(" + str(xt) + ", " + str(yt) + ", " + str(zt) + ")")))
    error += '\n\nAccuracy of the results improves as you provide more georeferenced points.\nWithout any additional georeferenced points, it is assumed that the model is scaled based on unit conversion and rotation is derived from TrueNorth direction (if available).\n'

    ifc_file = ifc_file.end_transaction()
    return messages, error

@app.route('/calc/<filename>', methods=['GET', 'POST'])
def calculate(filename):
    #if request.method == 'POST':
        # Access the form data by iterating through the rows
        coeff = session.get('coeff')
        rows = session.get('rows')
        fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        ifc_file = fileOpener(filename)
        data_points = []
        Refl = session.get('Refl')
        if Refl:
            xt = session.get('xt')
            yt = session.get('yt')
            zt = session.get('zt')
            bx = session.get('bx')
            by = session.get('by')
            bz = session.get('bz')
            data_points.append({"X": bx, "Y": by, "Z": bz, "X_prime": xt, "Y_prime": yt, "Z_prime":zt})
        #seperater
        if not Refl and rows == 1:
            Rotation_solution = 0
            S_solution = coeff
            ro = ifc_file.by_type("IfcGeometricRepresentationContext")[0].TrueNorth
            if ro is not None and ro.is_a("IfcDirection"):
                xord , xabs = round(float(ro[0][0]),6) , round(float(ro[0][1]),6)
            else:
                xord , xabs = 0 , 1
            Rotation_solution = math.atan2(xord,xabs)
            A = math.cos(Rotation_solution)
            B = math.sin(Rotation_solution)
            E_solution = float(request.form[f'x_prime{0}']) - (A*float(request.form[f'x{0}'])*coeff) + (B*float(request.form[f'y{0}'])*coeff)
            N_solution = float(request.form[f'y_prime{0}']) - (B*float(request.form[f'x{0}'])*coeff) - (A*float(request.form[f'y{0}'])*coeff)
            session['zt'] = float(request.form[f'z_prime{0}'])
            session['bz'] = float(request.form[f'z{0}'])
            H_solution = float(request.form[f'z_prime{0}']) - (float(request.form[f'z{0}'])*coeff)

        #seperater
        else:
            if rows == 0:
                Rotation_solution = 0
                S_solution = coeff
                ro = ifc_file.by_type("IfcGeometricRepresentationContext")[0].TrueNorth
                if ro is not None and ro.is_a("IfcDirection"):
                    xord , xabs = round(float(ro[0][0]),6) , round(float(ro[0][1]),6)
                else:
                    xord , xabs = 0 , 1
                Rotation_solution = math.atan2(xord,xabs)
                A = math.cos(Rotation_solution)
                B = math.sin(Rotation_solution)
                E_solution = xt - (A*S_solution*bx) + (B*S_solution*by)
                N_solution = yt - (B*S_solution*bx) - (A*S_solution*by)
                H_solution = zt - (S_solution*bz)
            else:
                for row in range(rows):
                    x = request.form[f'x{row}']
                    y = request.form[f'y{row}']
                    z = request.form[f'z{row}']
                    x_prime = request.form[f'x_prime{row}']
                    y_prime = request.form[f'y_prime{row}']
                    z_prime = request.form[f'z_prime{row}']

                    try:
                        x = float(x)
                        y = float(y)
                        z = float(z)
                        x_prime = float(x_prime)
                        y_prime = float(y_prime)
                        z_prime = float(z_prime)
                    except ValueError:
                        message = "Invalid input. Please enter only float values."
                        Num = rows
                        return render_template('survey.html', message=message, Num=Num)

                    data_points.append({"X": x, "Y": y, "Z":z,"X_prime": x_prime, "Y_prime": y_prime, "Z_prime": z_prime})

                def equations(variables, data_points):
                        S, Rotation, E, N , H = variables
                        eqs = []

                        for data in data_points:
                            X = data["X"]
                            Y = data["Y"]
                            Z = data["Z"]
                            X_prime = data["X_prime"]
                            Y_prime = data["Y_prime"]
                            Z_prime = data["Z_prime"]

                            eq1 = S * np.cos(Rotation) * X - S * np.sin(Rotation) * Y + E - X_prime
                            eq2 = S * np.sin(Rotation) * X + S * np.cos(Rotation) * Y + N - Y_prime
                            eq3 = S*Z + H - Z_prime
                            eqs.extend([eq1, eq2, eq3])

                        return eqs
                    # Initial guess for variables [S, Rotation, E, N]
                if Refl:
                    initial_guess = [coeff, 0, xt, yt, zt]
                else:
                    xg =float(request.form[f'x_prime{0}']) - (float(request.form[f'x{0}'])*coeff)
                    yg = float(request.form[f'y_prime{0}']) - (float(request.form[f'y{0}'])*coeff)
                    zg = float(request.form[f'z_prime{0}']) - (float(request.form[f'z{0}'])*coeff)
                    initial_guess = [coeff,0,xg,yg,zg]

                # Perform the least squares optimization for all data points
                result = leastsq(equations, initial_guess, args=(data_points,), full_output=True)
                S_solution, Rotation_solution, E_solution, N_solution, H_solution = result[0]

        Rotation_degrees = (180 / math.pi) * Rotation_solution
        rDeg = Rotation_degrees - (360*round(Rotation_degrees/360))

        target_epsg = "EPSG:"+str(session.get('target_epsg'))
        georeference_ifc.set_mapconversion_crs(ifc_file=ifc_file,
                                        target_crs_epsg_code=target_epsg,
                                        eastings=E_solution,
                                        northings=N_solution,
                                        orthogonal_height=H_solution,
                                        x_axis_abscissa=math.cos(Rotation_solution),
                                        x_axis_ordinate=math.sin(Rotation_solution),
                                        scale=S_solution)
        fn_output = re.sub(r'\.ifc$','_georeferenced.ifc', fn)
        ifc_file.write(fn_output)
        IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
        df = pd.DataFrame(list(IfcProjectedCRS.__dict__.items()), columns= ['property', 'value'])
        dg = pd.DataFrame(list(IfcMapConversion.__dict__.items()), columns= ['property', 'value'])
        dg['value'] = dg['value'].astype(str)
        html_table_f = df.to_html()
        html_table_g = dg.to_html()
        compliance = check_corenet_sg(ifc_file)
        return render_template('result.html', filename=filename, table_f=html_table_f, table_g=html_table_g, compliance=compliance)
    
def fileOpener(filename):
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        return ifcopenshell.open(fn)
    except Exception as e:
        app.logger.warning("Failed to open IFC %s: %s", fn, e)
        return None

@app.route('/show/<filename>', methods=['POST'])
def visualize(filename):
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    fn_output = re.sub(r'\.ifc$','_georeferenced.ifc', fn)
    if not os.path.exists(fn_output):
        fn_output = fn
    ifc_file = ifcopenshell.open(fn_output)
    IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
    target = IfcProjectedCRS.Name.split(':')
    org = ifc_file.by_type('IfcProject')[0].RepresentationContexts[0].WorldCoordinateSystem.Location.Coordinates
    E = IfcMapConversion.Eastings
    N = IfcMapConversion.Northings
    S = IfcMapConversion.Scale
    if S is None:
        S = 1
    ortz = IfcMapConversion.OrthogonalHeight
    cos = IfcMapConversion.XAxisAbscissa
    if cos is None:
        cos = 1    
    sin = IfcMapConversion.XAxisOrdinate
    if sin is None:
        sin = 0
    Rotation_solution = math.atan2(sin,cos)
    A = math.cos(Rotation_solution)
    B = math.sin(Rotation_solution)        
    target_epsg = "EPSG:"+ target[1]
    transformer2 = Transformer.from_crs(target_epsg,"EPSG:4326", always_xy=True)
    scaleError = session.get('scaleError')
    Gx , Gy = 0 , 0
    eff = session.get('coeff')
    if eff is None or eff == 0:
        eff = 1.0
    if scaleError:
        saver = S
        S = eff
        session.pop('scaleError', None)  # Corrected line
        E = E * S
        N = N * S
        ortz = ortz * S
        xx = S * org[0] * A - S * org[1] * B + E
        yy = S * org[0] * B + S * org[1] * A + N
        z = S * org[2] + ortz
        S = saver
        Snew = S
    else:
        xx = S * org[0]* A - S * org[1]*B + E
        yy = S * org[0]* B + S * org[1]*A + N
        zz = S * org[2] + ortz
        Snew = S/eff
    if xx==0 and yy==0:
        products = ifc_file.by_type('IfcProduct')
        for product in products:
            if product.Representation:
                placement = product.ObjectPlacement
                lpMAat = ifcopenshell.util.placement.get_local_placement(placement)
                Gx , Gy = lpMAat[0][3]*eff,lpMAat[1][3]*eff
                xx = xx+Gx
                yy = yy+Gy
                break
    lon,lat = transformer2.transform(xx,yy)
    Latitude = lat
    Longitude = lon
    Scale_value = pyproj.CRS(target_epsg).to_dict().get('k') or 1

    # Rotate the IFC's grid-north onto Mercator (Web map) north so the model
    # aligns with the basemap when projected. Sample a 1 km easting offset and
    # take its bearing in EPSG:3857.
    transformer3 = Transformer.from_crs(target_epsg, "EPSG:3857", always_xy=True)
    x_3857, y_3857 = transformer3.transform(xx, yy)
    xn_3857, yn_3857 = transformer3.transform(xx + 1000, yy)
    angle_radians = math.atan2(yn_3857 - y_3857, xn_3857 - x_3857)
    Rotation_solution = Rotation_solution + angle_radians

    min_z_value = float('inf')

    for product in ifc_file.by_type('IfcProduct'):
    # Check if the product has a placement
        if product.Representation:
            placement = product.ObjectPlacement
            location = ifcopenshell.util.placement.get_local_placement(placement)
            z_value = location[2][3]*eff  # Z is usually the 3rd coordinate (0: X, 1: Y, 2: Z)
            if z_value < min_z_value:
                min_z_value = z_value

    # Express IDs of IfcGeographicElement with ObjectType='SITEBOUNDARY' — used
    # by the viewer to subset the model down to just the site boundary geometry.
    siteboundary_ids = []
    try:
        for ge in ifc_file.by_type('IfcGeographicElement'):
            otype = (getattr(ge, 'ObjectType', '') or '').upper()
            if otype == 'SITEBOUNDARY' and ge.Representation is not None:
                siteboundary_ids.append(int(ge.id()))
    except Exception:
        pass

    return render_template('view3D.html', filename=filename, Latitude=Latitude, Longitude=Longitude, Rotate=Rotation_solution, origin = org, Scale = Snew, ScaleCRS = Scale_value, Gx=Gx, Gy=Gy, LowestLevel = min_z_value, SiteBoundaryIds=json.dumps(siteboundary_ids), maptiler_key=MAPTILER_KEY)

@app.route('/download/<filename>', methods=['GET'])
def download(filename):
    # Define the path to the GeoJSON file
    fn = re.sub(r'\\.ifc$','_georeferenced.ifc', filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], fn)

    # Ensure the file exists
    if os.path.exists(file_path):
        # Set the response headers to indicate a file download
        response = make_response()
        response.headers['Content-Type'] = 'application/octet-stream'
        response.headers['Content-Disposition'] = f'attachment; filename={fn}'
        
        # Read the file content and add it to the response
        with open(file_path, 'rb') as file:
            response.data = file.read()
        
        return response
    else:
        # Return a 404 error if the file doesn't exist
        return 'File not found', 404
@app.route('/templates/<path:filename>')
def temp(filename):
    return send_from_directory('templates', filename)
   
@app.route('/uploads/<path:filename>')
def ups(filename):
    return send_from_directory('uploads', filename)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
