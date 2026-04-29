#!/usr/bin/env python
"""Test script to diagnose template rendering issue."""

from pathlib import Path

def test_template():
    template_path = Path('ui/templates/index.html')
    
    try:
        # Test reading file
        content = template_path.read_text()
        print(f'✅ File read successfully: {len(content)} bytes')
        
        # Check ending
        if content.strip().endswith('</html>'):
            print('✅ File ends with</html>')
        else:
            print('❌ File does NOT end with</html>')
            print(f'Last 50 chars: {content[-50:]}')
        
        # Try HTMLResponse
        from fastapi.responses import HTMLResponse
        response = HTMLResponse(content=content)
        print('✅ HTMLResponse created successfully')
        
        # Try serving it
        from fastapi import FastAPI
        app = FastAPI()
        
        @app.get("/")
        def serve():
            return HTMLResponse(content=content)
        
        print('✅ FastAPI route defined successfully')
        
    except Exception as e:
        print(f'❌ Error: {e}')
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == '__main__':
    success = test_template()
    exit(0 if success else 1)
