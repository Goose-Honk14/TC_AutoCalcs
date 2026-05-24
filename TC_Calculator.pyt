import arcpy
import math
import os

class Toolbox(object):
    def __init__(self):
        self.label = "TC Calculator"
        self.alias = "tc_calculator"
        self.tools = [TCCalculator]

class TCCalculator(object):
    def __init__(self):
        self.label = "Calculate Time of Concentration"
        self.description = "Calculate TC times for segments and aggregate by basin using SCS TR-55 methodology"
        self.canRunInBackground = False

    def getParameterInfo(self):
        # Parameter 0: Input Geodatabase
        param0 = arcpy.Parameter(
            displayName="Input Geodatabase",
            name="input_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        param0.filter.list = ["Local Database"]

        # Parameter 1: Feature Class Name
        param1 = arcpy.Parameter(
            displayName="TC Links Feature Class",
            name="feature_class",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )

        # Parameter 2: Output Geodatabase
        param2 = arcpy.Parameter(
            displayName="Output Geodatabase",
            name="output_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        param2.filter.list = ["Local Database"]

        # Parameter 3: Precipitation (inches)
        param3 = arcpy.Parameter(
            displayName="Precipitation (24-hr storm event, inches)",
            name="precipitation",
            datatype="GPDouble",
            parameterType="Required",
            direction="Input"
        )
        param3.value = 4.35

        return [param0, param1, param2, param3]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        input_gdb = parameters[0].valueAsText
        fc_name = parameters[1].valueAsText
        output_gdb = parameters[2].valueAsText
        precipitation = parameters[3].value

        try:
            arcpy.env.workspace = input_gdb
            
            # Build feature class path
            fc_path = os.path.join(input_gdb, fc_name)
            
            arcpy.AddMessage(f"Processing feature class: {fc_path}")
            arcpy.AddMessage(f"Precipitation: {precipitation} inches")
            arcpy.AddMessage(f"Minimum Tc: 10 minutes")
            
            # Manning's n coefficients for Sheet Flow
            sheet_flow_n = {
                'a': 0.011,
                'b': 0.05,
                'c': 0.06,
                'd': 0.17,
                'e': 0.15,
                'f': 0.24,
                'g': 0.41,
                'h': 0.4,
                'i': 0.8,
                'j': 0.13
            }
            
            # Shallow Concentrated Flow velocities (ft/s)
            shallow_conc_velocity = {
                'p': 1.96,
                'u': 1.13
            }
            
            # Fixed velocities
            PIPE_VELOCITY = 2.50  # ft/s
            CHANNEL_VELOCITY = 2.00  # ft/s
            MIN_TC = 10  # minutes
            
            # Dictionary to store basin TC times
            basin_tc_dict = {}
            
            # Check if output fields exist, if not create them
            fields = [f.name for f in arcpy.ListFields(fc_path)]
            
            if 'SEGMENT_TC' not in fields:
                arcpy.AddField_management(fc_path, 'SEGMENT_TC', 'DOUBLE')
                arcpy.AddMessage("Created SEGMENT_TC field")
            
            if 'BASIN_TC' not in fields:
                arcpy.AddField_management(fc_path, 'BASIN_TC', 'DOUBLE')
                arcpy.AddMessage("Created BASIN_TC field")
            
            # First pass: Calculate segment TC times
            with arcpy.da.UpdateCursor(fc_path, ['SEGMENT_ID', 'FLOWTYPE', 'SURFCODE', 'UPSTREAM_E', 'DOWNSTREAM_E', 'Shape_Length', 'BASIN_NAME', 'SEGMENT_TC']) as cursor:
                for row in cursor:
                    segment_id, flowtype, surfcode, upstream_e, downstream_e, shape_length, basin_name, segment_tc = row
                    
                    try:
                        # Calculate slope
                        if shape_length == 0 or shape_length is None:
                            arcpy.AddWarning(f"Segment {segment_id}: Shape_Length is 0 or null")
                            segment_tc = MIN_TC
                        else:
                            elevation_diff = abs(upstream_e - downstream_e)
                            slope = elevation_diff / shape_length if shape_length > 0 else 0
                            
                            # Calculate TC based on flow type
                            if flowtype == 1:  # Sheet Flow
                                # Manning's equation: Tt = (0.007 * n * L) / (sqrt(S) * sqrt(P))
                                # where: n = Manning's coefficient, L = length (ft), S = slope (ft/ft), P = rainfall (in)
                                if surfcode.lower() in sheet_flow_n:
                                    n = sheet_flow_n[surfcode.lower()]
                                    if slope > 0 and precipitation > 0:
                                        segment_tc = (0.007 * n * shape_length) / (math.sqrt(slope) * math.sqrt(precipitation))
                                    else:
                                        segment_tc = MIN_TC
                                else:
                                    arcpy.AddWarning(f"Segment {segment_id}: Invalid SURFCODE '{surfcode}' for Sheet Flow")
                                    segment_tc = MIN_TC
                            
                            elif flowtype == 2:  # Shallow Concentrated Flow
                                # Tt = L / (V * 60), where V is in ft/s, convert to minutes
                                if surfcode.lower() in shallow_conc_velocity:
                                    velocity = shallow_conc_velocity[surfcode.lower()]
                                    segment_tc = (shape_length / velocity) / 60  # Convert seconds to minutes
                                else:
                                    arcpy.AddWarning(f"Segment {segment_id}: Invalid SURFCODE '{surfcode}' for Shallow Concentrated Flow")
                                    segment_tc = MIN_TC
                            
                            elif flowtype == 3:  # Pipe Flow
                                # Tt = L / (V * 60)
                                segment_tc = (shape_length / PIPE_VELOCITY) / 60
                            
                            elif flowtype == 4:  # Pond Flow
                                # Add 0 time (ignored for now)
                                segment_tc = 0
                            
                            elif flowtype == 5:  # Channel Flow
                                # Tt = L / (V * 60)
                                segment_tc = (shape_length / CHANNEL_VELOCITY) / 60
                            
                            else:
                                arcpy.AddWarning(f"Segment {segment_id}: Invalid FLOWTYPE '{flowtype}'")
                                segment_tc = MIN_TC
                        
                        # Apply minimum TC
                        if segment_tc < MIN_TC and flowtype != 4:  # Don't apply minimum to pond flow (0)
                            segment_tc = MIN_TC
                        
                        # Store in dictionary for basin aggregation
                        if basin_name not in basin_tc_dict:
                            basin_tc_dict[basin_name] = 0
                        basin_tc_dict[basin_name] += segment_tc
                        
                        # Update row
                        row[7] = segment_tc
                        cursor.updateRow(row)
                        
                        arcpy.AddMessage(f"Segment {segment_id}: TC = {segment_tc:.2f} min (FLOWTYPE={flowtype}, SURFCODE={surfcode})")
                    
                    except Exception as e:
                        arcpy.AddError(f"Error processing Segment {segment_id}: {str(e)}")
                        segment_tc = MIN_TC
                        row[7] = segment_tc
                        cursor.updateRow(row)
            
            # Second pass: Update BASIN_TC field
            with arcpy.da.UpdateCursor(fc_path, ['BASIN_NAME', 'BASIN_TC']) as cursor:
                for row in cursor:
                    basin_name, basin_tc = row
                    if basin_name in basin_tc_dict:
                        row[1] = basin_tc_dict[basin_name]
                        cursor.updateRow(row)
            
            arcpy.AddMessage("\n=== Basin TC Summary ===")
            for basin_name, basin_tc in sorted(basin_tc_dict.items()):
                arcpy.AddMessage(f"Basin: {basin_name} | Total TC: {basin_tc:.2f} minutes")
            
            arcpy.AddMessage("\nTC Calculation completed successfully!")
        
        except Exception as e:
            arcpy.AddError(f"Error: {str(e)}")
            raise
