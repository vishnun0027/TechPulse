import os
import subprocess
import shutil
from pathlib import Path
from dotenv import load_dotenv

def main():
    print("🚀 Starting Pulse Release Build...")
    
    # 1. Load keys from .env
    load_dotenv()
    url = os.environ.get("SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    
    if not url or not anon_key:
        print("❌ Error: Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env")
        print("Make sure you have these in your .env file before building a release.")
        return
        
    print(f"🔒 Found public keys for: {url}")
    print("💉 Injecting public keys into build temporarily...")
    
    # 2. Read the target file
    target_path = Path("src/cli/user.py")
    with open(target_path, "r") as f:
        original_code = f.read()
        
    # 3. Inject the keys
    modified_code = original_code.replace(
        'url = os.environ.get("SUPABASE_URL") or settings.supabase_url',
        f'url = "{url}"'
    ).replace(
        'anon_key = os.environ.get("SUPABASE_ANON_KEY") or settings.supabase_anon_key',
        f'anon_key = "{anon_key}"'
    )
    
    with open(target_path, "w") as f:
        f.write(modified_code)
        
    try:
        # 4. Run PyInstaller
        print("📦 Running PyInstaller (this may take a minute)...")
        # We use standard PyInstaller, calling it via uv run
        subprocess.run(
            ["uv", "run", "pyinstaller", "--name", "pulse", "--onefile", "src/cli/user.py"], 
            check=True
        )
        print("✅ Build complete! Your standalone binary is in the 'dist/pulse' folder.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Build failed with error: {e}")
    finally:
        # 5. RESTORE THE SOURCE CODE (Critical for security)
        print("🧹 Restoring source code to secure state...")
        with open(target_path, "w") as f:
            f.write(original_code)
            
        # Clean up PyInstaller build files to keep the repo neat
        if Path("build").exists():
            shutil.rmtree("build")
        if Path("pulse.spec").exists():
            os.remove("pulse.spec")
            
        print("🎉 Done! The Real-World binary is ready.")

if __name__ == "__main__":
    main()
