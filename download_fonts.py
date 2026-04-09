import os
import urllib.request
import re

# Create fonts directory
fonts_dir = os.path.join('ui', 'static', 'fonts')
os.makedirs(fonts_dir, exist_ok=True)

# Google Fonts CSS URL
google_fonts_url = 'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Syne:wght@400;500;600;700&display=swap'

# Fetch the CSS file
print(f"Fetching CSS from: {google_fonts_url}")
headers = {'User-Agent': 'Mozilla/5.0'}
req = urllib.request.Request(google_fonts_url, headers=headers)
with urllib.request.urlopen(req) as response:
    css_content = response.read().decode('utf-8')

print("\nCSS Content received:")
print("-" * 80)
print(css_content[:500])
print("-" * 80)

# Find all font URLs in the CSS
font_urls = re.findall(r'url\((https://[^)]+)\)', css_content)
print(f"\nFound {len(font_urls)} font files to download")

# Download each font file
downloaded_fonts = {}
for i, font_url in enumerate(font_urls, 1):
    # Extract filename from URL
    filename = font_url.split('/')[-1]
    if '?' in filename:
        filename = filename.split('?')[0]
    
    # Ensure .woff2 extension
    if not filename.endswith('.woff2'):
        filename += '.woff2'
    
    local_path = os.path.join(fonts_dir, filename)
    
    print(f"\n[{i}/{len(font_urls)}] Downloading: {filename}")
    print(f"  From: {font_url}")
    
    try:
        urllib.request.urlretrieve(font_url, local_path)
        print(f"  Saved to: {local_path}")
        downloaded_fonts[font_url] = f'fonts/{filename}'
    except Exception as e:
        print(f"  Error: {e}")

# Create local CSS file with updated paths
local_css = css_content
for original_url, local_path in downloaded_fonts.items():
    local_css = local_css.replace(original_url, local_path)

# Save the local CSS
css_output_path = os.path.join('ui', 'static', 'css', 'fonts.css')
with open(css_output_path, 'w', encoding='utf-8') as f:
    f.write(local_css)

print(f"\n✓ Local CSS file created: {css_output_path}")
print(f"✓ Downloaded {len(downloaded_fonts)} font files to {fonts_dir}")
print("\nReplace the Google Fonts import with:")
print(f'  <link rel="stylesheet" href="/static/css/fonts.css">')
print("or in CSS:")
print(f'  @import url("/static/css/fonts.css");')
