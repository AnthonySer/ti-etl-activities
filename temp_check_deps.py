import sys
try:
    import docx
    print('python-docx version:', docx.__version__)
except ImportError as e:
    print("python-docx no está instalado:", e)
try:
    import openpyxl
    print('openpyxl version:', openpyxl.__version__)
except ImportError as e:
    print("openpyxl no está instalado:", e)
