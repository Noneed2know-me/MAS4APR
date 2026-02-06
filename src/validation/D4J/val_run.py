import json
import os
from tqdm import tqdm
import json
from typing import List, Any


def extract_name(s):
    return s.split('-')[0]


import subprocess


def generate_and_execute_commands(bug_name, pass_count, fail_count, fail_case_list):
    bug_id = bug_name.replace("_", "-")
    project = bug_id.split("-")[0].lower() + bug_id.split("-")[1]

    

    commands = [
        f"python /MAS4APR/src/validation/D4J/val_d4j.py -i /MAS4APR/src/output/patches/{bug_name} -o /MAS4APR/src/validation/D4J/validation/{project}_patches_val -d /MAS4APR/D4J_dataset/defects4j-sf.json"
    ]

    # 执行命令
    for i, cmd in enumerate(commands, 1):
        print(f"Executing {i}:")
        print(cmd)
        try:
            result = subprocess.run(cmd, shell=True, check=True, text=True, capture_output=True)
            print("Output:")
            print(result.stdout)
            if "Plausible found" in result.stdout:
                pass_count += 1
            else:
                fail_count += 1
                fail_case_list.append(bug_id)
        except subprocess.CalledProcessError as e:
            print(f"Exception info:")
            print(e.output)
        print()
        
    return pass_count, fail_count, fail_case_list



def write_list_to_json(data: List[Any], file_path: str, pretty: bool = True) -> None:
    """
    Write a Python list to a JSON file.

    Parameters:
        data (List[Any]): The list to serialize into JSON.
        file_path (str): Where to save the JSON file.
        pretty (bool): Whether to format the JSON with indentation.

    Raises:
        TypeError: If the data contains non-serializable elements.
        IOError: If the file cannot be written.
    """
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            if pretty:
                json.dump(data, f, ensure_ascii=False, indent=4)
            else:
                json.dump(data, f, ensure_ascii=False)
    except TypeError as e:
        raise TypeError(f"List contains non-serializable data: {e}")
    except IOError as e:
        raise IOError(f"Failed to write to {file_path}: {e}")


def convert_chart_string(input_string):
    parts = input_string.split('_')
    if len(parts) > 1:
        prefix = parts[0].capitalize()
        number = ''.join(filter(str.isdigit, prefix))
        suffix = parts[1].split('.')[0]
        return f'{prefix[:-len(number)]}-{number}'
    else:
        return input_string

patch_path = "/MAS4APR/src/output/patches/"
files =os.listdir(patch_path) 
files.sort()

i = 1
pass_count = 0
fail_count = 0
fail_case_list = []
for filename in files:
    if filename.endswith(".json"):
        bug_name = convert_chart_string(filename)
        print(bug_name)
        print(i)
        i += 1
        pass_count, fail_count, fail_case_list = generate_and_execute_commands(bug_name, pass_count, fail_count, fail_case_list)

write_list_to_json(fail_case_list, "/MAS4APR/failed_project.json")
print(f'[CASE PASSED]: case patched = {pass_count} out of [TOTAL CASE]: total case {i-1} | [CASE FAILED]: failed case = {fail_count}')