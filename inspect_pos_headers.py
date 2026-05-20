import openpyxl

path = r"E:\Pricing\Monthly\JAN-2026\0.30UP POSITION REPORT 01-01-2026.xlsx"
wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
ws = wb.active

headers = []
for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
    headers = list(row)

wb.close()

print("Total columns:", len(headers))
print()
# Print all headers with index
for i, h in enumerate(headers):
    if h is not None:
        print(f"  col {i:3d}: {h}")
