import arcpy
import math
import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from datetime import datetime

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

        # Parameter 1: Feature Class Name (Optional - will auto-detect TC if not provided)
        param1 = arcpy.Parameter(
            displayName="TC Links Feature Class (leave blank to auto-detect 'TC')",
            name="feature_class",
            datatype="GPString",
            parameterType="Optional",
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

        # Parameter 4: Export to Excel
        param4 = arcpy.Parameter(
            displayName="Export Results to Excel",
            name="export_excel",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param4.value = True

        # Parameter 5: Output Excel Directory (Optional - will auto-generate filename if not provided)
        param5 = arcpy.Parameter(
            displayName="Output Excel Directory (leave blank for auto-generated file)",
            name="excel_directory",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input"
        )

        return [param0, param1, param2, param3, param4, param5]

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
        export_excel = parameters[4].value
        excel_directory = parameters[5].valueAsText

        try:
            arcpy.env.workspace = input_gdb
            
            # Auto-detect TC feature class if not provided
            if not fc_name or fc_name.strip() == "":
                fc_name = self._find_tc_feature_class(input_gdb, arcpy)
                if not fc_name:
                    arcpy.AddError("Could not find 'TC' feature class in the geodatabase or its feature datasets. Please specify the feature class name.")
                    raise Exception("Feature class 'TC' not found")
                arcpy.AddMessage(f"Auto-detected feature class: {fc_name}")
            
            # Build feature class path
            fc_path = os.path.join(input_gdb, fc_name)
            
            arcpy.AddMessage(f"Processing feature class: {fc_path}")
            arcpy.AddMessage(f"Precipitation: {precipitation} inches")
            arcpy.AddMessage(f"Minimum Tc (Basin): 10 minutes")
            
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
            MIN_TC_BASIN = 10  # minutes (applied to final basin TC)
            
            # Dictionary to store basin TC times
            basin_tc_dict = {}
            # List to store segment details for Excel export
            segment_details = []
            
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
                            segment_tc = 0
                            flow_type_name = "Unknown"
                        else:
                            elevation_diff = abs(upstream_e - downstream_e)
                            slope = elevation_diff / shape_length if shape_length > 0 else 0
                            
                            # Calculate TC based on flow type
                            if flowtype == 1:  # Sheet Flow
                                flow_type_name = "Sheet Flow"
                                # Manning's equation: Tt = (0.007 * n * L) / (sqrt(S) * sqrt(P))
                                # where: n = Manning's coefficient, L = length (ft), S = slope (ft/ft), P = rainfall (in)
                                if surfcode.lower() in sheet_flow_n:
                                    n = sheet_flow_n[surfcode.lower()]
                                    if slope > 0 and precipitation > 0:
                                        segment_tc = (0.007 * n * shape_length) / (math.sqrt(slope) * math.sqrt(precipitation))
                                    else:
                                        segment_tc = 0
                                else:
                                    arcpy.AddWarning(f"Segment {segment_id}: Invalid SURFCODE '{surfcode}' for Sheet Flow")
                                    segment_tc = 0
                            
                            elif flowtype == 2:  # Shallow Concentrated Flow
                                flow_type_name = "Shallow Concentrated Flow"
                                # Tt = L / (V * 60), where V is in ft/s, convert to minutes
                                if surfcode.lower() in shallow_conc_velocity:
                                    velocity = shallow_conc_velocity[surfcode.lower()]
                                    segment_tc = (shape_length / velocity) / 60  # Convert seconds to minutes
                                else:
                                    arcpy.AddWarning(f"Segment {segment_id}: Invalid SURFCODE '{surfcode}' for Shallow Concentrated Flow")
                                    segment_tc = 0
                            
                            elif flowtype == 3:  # Pipe Flow
                                flow_type_name = "Pipe Flow"
                                # Tt = L / (V * 60)
                                segment_tc = (shape_length / PIPE_VELOCITY) / 60
                            
                            elif flowtype == 4:  # Pond Flow
                                flow_type_name = "Pond Flow"
                                # Add 0 time (ignored for now)
                                segment_tc = 0
                            
                            elif flowtype == 5:  # Channel Flow
                                flow_type_name = "Channel Flow"
                                # Tt = L / (V * 60)
                                segment_tc = (shape_length / CHANNEL_VELOCITY) / 60
                            
                            else:
                                flow_type_name = "Invalid"
                                arcpy.AddWarning(f"Segment {segment_id}: Invalid FLOWTYPE '{flowtype}'")
                                segment_tc = 0
                        
                        # Store segment details for Excel export
                        segment_details.append({
                            'segment_id': segment_id,
                            'basin_name': basin_name,
                            'flowtype': flowtype,
                            'flow_type_name': flow_type_name,
                            'surfcode': surfcode,
                            'length': shape_length,
                            'upstream_elev': upstream_e,
                            'downstream_elev': downstream_e,
                            'segment_tc': segment_tc
                        })
                        
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
                        segment_tc = 0
                        row[7] = segment_tc
                        cursor.updateRow(row)
            
            # Second pass: Update BASIN_TC field with minimum applied
            with arcpy.da.UpdateCursor(fc_path, ['BASIN_NAME', 'BASIN_TC']) as cursor:
                for row in cursor:
                    basin_name, basin_tc = row
                    if basin_name in basin_tc_dict:
                        final_basin_tc = basin_tc_dict[basin_name]
                        # Apply 10-minute minimum to final basin TC
                        if final_basin_tc < MIN_TC_BASIN:
                            final_basin_tc = MIN_TC_BASIN
                        row[1] = final_basin_tc
                        cursor.updateRow(row)
            
            arcpy.AddMessage("\n=== Basin TC Summary ===")
            basin_summary = []
            for basin_name in sorted(basin_tc_dict.keys()):
                basin_tc = basin_tc_dict[basin_name]
                # Apply 10-minute minimum for display
                if basin_tc < MIN_TC_BASIN:
                    final_basin_tc = MIN_TC_BASIN
                else:
                    final_basin_tc = basin_tc
                arcpy.AddMessage(f"Basin: {basin_name} | Total TC: {final_basin_tc:.2f} minutes")
                basin_summary.append({
                    'basin_name': basin_name,
                    'total_tc': basin_tc,
                    'final_basin_tc': final_basin_tc
                })
            
            # Generate or use provided Excel file path
            excel_path = None
            if export_excel:
                if excel_directory:
                    # Generate filename based on timestamp
                    excel_filename = f"TC_Results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    excel_path = os.path.join(excel_directory, excel_filename)
                else:
                    # Use output GDB directory if no directory specified
                    output_dir = os.path.dirname(output_gdb)
                    excel_filename = f"TC_Results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    excel_path = os.path.join(output_dir, excel_filename)
                
                self._export_to_excel(excel_path, segment_details, basin_summary, precipitation, MIN_TC_BASIN, arcpy)
                arcpy.AddMessage(f"\nExcel report exported to: {excel_path}")
            
            arcpy.AddMessage("\nTC Calculation completed successfully!")
        
        except Exception as e:
            arcpy.AddError(f"Error: {str(e)}")
            raise

    def _find_tc_feature_class(self, gdb_path, arcpy):
        """
        Search for a feature class named 'TC' in the geodatabase.
        Searches both root level and inside feature datasets.
        Returns the full path to the feature class if found, otherwise None.
        """
        try:
            arcpy.env.workspace = gdb_path
            
            # First, search for TC at root level
            feature_classes = arcpy.ListFeatureClasses()
            for fc in feature_classes:
                if fc.lower() == 'tc':
                    return fc
            
            # Then search within feature datasets
            feature_datasets = arcpy.ListDatasets('', 'Feature')
            for fds in feature_datasets:
                arcpy.env.workspace = os.path.join(gdb_path, fds)
                feature_classes = arcpy.ListFeatureClasses()
                for fc in feature_classes:
                    if fc.lower() == 'tc':
                        # Return the full path relative to the GDB
                        return f"{fds}/{fc}"
            
            # Reset workspace
            arcpy.env.workspace = gdb_path
            return None
        except Exception as e:
            arcpy.AddError(f"Error searching for feature classes: {str(e)}")
            return None

    def _export_to_excel(self, excel_path, segment_details, basin_summary, precipitation, min_tc_basin, arcpy):
        """Export calculation results to Excel workbook"""
        try:
            wb = Workbook()
            
            # Remove default sheet
            if 'Sheet' in wb.sheetnames:
                wb.remove(wb['Sheet'])
            
            # Create Segment Details sheet
            ws_segments = wb.create_sheet('Segment Details', 0)
            self._create_segment_sheet(ws_segments, segment_details)
            
            # Create Basin Summary sheet
            ws_basin = wb.create_sheet('Basin Summary', 1)
            self._create_basin_sheet(ws_basin, basin_summary, precipitation, min_tc_basin)
            
            # Create Summary Info sheet
            ws_info = wb.create_sheet('Summary Info', 2)
            self._create_info_sheet(ws_info, precipitation, len(segment_details), len(basin_summary))
            
            # Save workbook
            wb.save(excel_path)
            
        except Exception as e:
            arcpy.AddError(f"Error exporting to Excel: {str(e)}")
            raise

    def _create_segment_sheet(self, ws, segment_details):
        """Create segment details worksheet"""
        # Define styles
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Headers
        headers = ['Segment ID', 'Basin Name', 'Flow Type', 'Surface Code', 'Length (ft)', 
                   'Upstream Elev (ft)', 'Downstream Elev (ft)', 'Slope (ft/ft)', 'TC (minutes)']
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        # Data rows
        for row_idx, segment in enumerate(segment_details, 2):
            slope = 0
            if segment['length'] > 0:
                slope = abs(segment['upstream_elev'] - segment['downstream_elev']) / segment['length']
            
            values = [
                segment['segment_id'],
                segment['basin_name'],
                segment['flow_type_name'],
                segment['surfcode'],
                segment['length'],
                segment['upstream_elev'],
                segment['downstream_elev'],
                slope,
                segment['segment_tc']
            ]
            
            for col, value in enumerate(values, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.border = border
                cell.alignment = Alignment(horizontal='center', vertical='center')
                
                # Format numbers
                if col in [5, 6, 7, 8, 9]:
                    cell.number_format = '0.00'
        
        # Adjust column widths
        ws.column_dimensions['A'].width = 12
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 22
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 14
        ws.column_dimensions['F'].width = 16
        ws.column_dimensions['G'].width = 16
        ws.column_dimensions['H'].width = 14
        ws.column_dimensions['I'].width = 14

    def _create_basin_sheet(self, ws, basin_summary, precipitation, min_tc_basin):
        """Create basin summary worksheet"""
        # Define styles
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Headers
        headers = ['Basin Name', 'Sum of Segment TC (min)', 'Applied Minimum TC (min)', 'Final Basin TC (min)']
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        # Data rows
        for row_idx, basin in enumerate(basin_summary, 2):
            values = [
                basin['basin_name'],
                basin['total_tc'],
                min_tc_basin,
                basin['final_basin_tc']
            ]
            
            for col, value in enumerate(values, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.border = border
                cell.alignment = Alignment(horizontal='center', vertical='center')
                
                # Format numbers
                if col in [2, 3, 4]:
                    cell.number_format = '0.00'
        
        # Adjust column widths
        ws.column_dimensions['A'].width = 15
        ws.column_dimensions['B'].width = 25
        ws.column_dimensions['C'].width = 22
        ws.column_dimensions['D'].width = 20

    def _create_info_sheet(self, ws, precipitation, num_segments, num_basins):
        """Create summary information worksheet"""
        # Define styles
        title_font = Font(bold=True, size=14, color="FFFFFF")
        title_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        label_font = Font(bold=True)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Title
        ws.merge_cells('A1:B1')
        title_cell = ws['A1']
        title_cell.value = "TC Calculation Summary"
        title_cell.font = title_font
        title_cell.fill = title_fill
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 25
        
        # Summary information
        info_data = [
            ('Calculation Date:', datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ('Precipitation (24-hr):', f"{precipitation} inches"),
            ('Number of Segments:', num_segments),
            ('Number of Basins:', num_basins),
            ('Minimum Basin TC:', "10 minutes"),
            ('Methodology:', "SCS TR-55")
        ]
        
        for row_idx, (label, value) in enumerate(info_data, 3):
            # Label
            label_cell = ws.cell(row=row_idx, column=1, value=label)
            label_cell.font = label_font
            label_cell.border = border
            label_cell.alignment = Alignment(horizontal='right', vertical='center')
            
            # Value
            value_cell = ws.cell(row=row_idx, column=2, value=value)
            value_cell.border = border
            value_cell.alignment = Alignment(horizontal='left', vertical='center')
        
        # Adjust column widths
        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 35
