import sys
print(f"Python Executable: {sys.executable}")
print("Importing modules...")

try:
    import fastapi
    print("✓ fastapi imported")
    import uvicorn
    print("✓ uvicorn imported")
    import supabase
    print("✓ supabase imported")
    import google.generativeai
    print("✓ google.generativeai imported")
    import db_utils
    print("✓ db_utils imported")
    import server 
    print("✓ server imported (syntax check complete)")
    
    print("ALL IMPORTS SUCCESSFUL")
except Exception as e:
    print(f"❌ IMPORT ERROR: {e}")
    import traceback
    traceback.print_exc()
