import os
import subprocess

def clone_defects4j(version_tag, target_dir):
    if not os.path.exists(target_dir):
        print(f"Cloning Defects4J {version_tag} into {target_dir}...")
        subprocess.run(["git", "clone", "https://github.com/rjust/defects4j.git", target_dir])
    else:
        print(f"{target_dir} already exists. Skipping clone.")

    os.chdir(target_dir)
    subprocess.run(["git", "checkout", version_tag])

    print("Installing dependencies...")
    subprocess.run(["./init.sh"])
    os.chdir("..")


# Download v1.2 and v2.0
clone_defects4j("v1.2.0", "defects4j_v1.2")
clone_defects4j("v2.0.0", "defects4j_v2.0")