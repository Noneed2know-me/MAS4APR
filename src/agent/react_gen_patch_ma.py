"""
Multi-agent extension of the original react_sf_gen_patch script.

This script introduces separate Coordinator and Custodian agents to perform
different roles in the software defect analysis and repair pipeline.
The Coordinator analyzes the root cause and suggests multiple repair
directions, while the Custodian retrieves reusable internal components
from the codebase that may help implement those directions. The rest
of the workflow (patch generation) remains largely unchanged.

The file retains compatibility with the original dataset structure
and logging conventions while integrating the new agent roles.
"""

import sys
import time
from typing import Union

import openai
import os
from langchain_core.prompts import PromptTemplate
from langchain_community.callbacks import get_openai_callback
from langchain.agents import AgentExecutor, create_react_agent
from langchain_openai import ChatOpenAI

import re
import json
from datetime import datetime
from src.config import OPENAI_API_KEY
from .tools import *

# Configure the OpenAI API key so that the underlying calls work.
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY


def extract_and_save_final_answer(text: str, output_file: str, name: str) -> dict:
    """
    Extracts the root cause and suggestions from a Coordinator agent's final
    answer and saves them into a JSON file. The JSON is keyed by bug
    name with the root cause mapping to a list of suggestion strings.

    Parameters
    ----------
    text : str
        The Coordinator agent's output containing "Root Cause" and
        "Suggestion" fields.
    output_file : str
        Path to a JSON file where extracted analyses are stored. If it
        exists, it will be read and updated; otherwise, a new file is
        created.
    name : str
        The identifier for the current bug used as the key in the JSON.

    Returns
    -------
    dict
        The updated analyses dictionary after saving.
    """
    root_cause_match = re.search(r"(?i)root\s*cause:?\s*(.+?)(?=\n+ *suggestion|\Z)", text, re.DOTALL | re.IGNORECASE)
    suggestions_matches = re.finditer(r"(?i)suggestion\s*(\d+):?\s*(.*?)(?=\n\s*suggestion|\Z)", text,
                                      re.DOTALL | re.IGNORECASE)

    if not root_cause_match:
        raise ValueError("Root Cause not found in the text")

    root_cause = root_cause_match.group(1).strip()
    suggestions = []

    for match in suggestions_matches:
        suggestion_number = match.group(1)
        suggestion_text = match.group(2).strip()
        lines = suggestion_text.split('\n', 1)
        title = lines[0].strip()
        details = lines[1].strip() if len(lines) > 1 else ""
        suggestions.append("Suggestion " + suggestion_number + ". " + title + " " + details + "\n")

    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            all_analyses = json.load(f)
    else:
        all_analyses = {}

    if name in all_analyses:
        all_analyses[name][root_cause] = suggestions
    else:
        all_analyses[name] = {root_cause: suggestions}

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_analyses, f, ensure_ascii=False, indent=2)

    return all_analyses


def save_custodian_components(text: str, output_file: str, bug_id: str) -> None:
    """
    Saves the custodian agent's output into a JSON file. The custodian
    output is expected to be valid JSON describing reusable components.
    If parsing fails, the raw text is stored instead for debugging.

    Parameters
    ----------
    text : str
        The raw output from the custodian agent. Ideally a JSON string.
    output_file : str
        File path where the custodian output will be saved.
    bug_id : str
        Identifier of the current bug, stored in the JSON if parsing
        fails.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"bug_id": bug_id, "raw_output": text}

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_patches(result: str) -> list:
    """
    Extracts patch contents enclosed within [START PATCH N] ... [END PATCH N]
    markers from a string. Returns a list of patch strings.
    """
    pattern = r'\[START PATCH \d+\]\n```\w*\n(.*?)\n```\n\[END PATCH \d+\]'
    patches = re.findall(pattern, result, re.DOTALL)
    return [patch.strip() for patch in patches]


def api_gpt_response(prompt: str, n: int):
    """
    Helper for patch generation. Invokes the OpenAI responses API to
    generate `n` completions for the given prompt. Uses gpt-5 by
    default with a temperature of 0.8.
    """
    response = openai.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="gpt-5",
        n=n)
    return response


def append_patches_to_json(patches: list, file_path: str, project_name: str) -> None:
    """
    Appends a list of patches to a JSON file, keyed by project name. If
    the file does not exist, it is created. If the project name does
    not exist within the file, it is initialized.
    """
    if not os.path.exists(file_path):
        data = {}
    else:
        with open(file_path, 'r') as f:
            data = json.load(f)
    if project_name not in data:
        data[project_name] = {"patches": []}
    data[project_name]["patches"].extend(patches)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)


def load_retrieved_components_context(base_dir: str, name: str) -> str:
    """
    Reads:
      {base_dir}/{name}.json
    and returns a string containing only:
      - name
      - location
      - related_direction
    """
    path = os.path.join(base_dir, f"{name}.json")
    if not os.path.exists(path):
        return "(No pre-collected components file found.)"

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    comps = data.get("components", [])
    if not comps:
        return "(Pre-collected components file is empty.)"

    lines = []
    for i, c in enumerate(comps, start=1):
        comp_name = str(c.get("name", "")).strip()
        location = str(c.get("location", "")).strip()
        description = str(c.get("description", "")).strip()
        rd = str(c.get("related_direction", "")).strip()

        # Only include the requested fields
        lines.append(
            f"{i}. name: {comp_name}\n"
            f"   location: {location}\n"
            f"   description: {description}\n"
            f"   related_direction: {rd}"
        )

    return "\n".join(lines)


# Assemble the set of tools available to both agents. Tools are imported
# from the sibling module '.tools'.
tools = [open_proj_tool, trace_method_usage_tool, find_method_in_file_tool, analyze_method_details_tool, analyze_method_control_flow_tool,
         find_class_loc_tool, identify_class_tool, get_imports_tool, close_proj_tool]

# Precompute tool descriptions and names for prompt insertion.
tool_descriptions = "\n".join(
    f"{i + 1}. {tool.description}\n" for i, tool in enumerate(tools)
)
tool_names = "\n".join(
    f"{i + 1}. {tool.name}\n" for i, tool in enumerate(tools)
)


manager_prompt_template = PromptTemplate.from_template(
    """
You are acting as the **Coordinator** in a multi-agent software repair team.
Your responsibilities are:
- Analyze the given Java code and defect information.
- Use the available tools to understand the bug.
- Identify and explain the *root cause* of the defect.
- Propose **three independent directions** (high-level strategies) to fix the defect.
- For each direction, provide a concrete, detailed suggestion that can later guide patch generation.
- Based on the patch suggestion, please isolate the code region to be modified as follows: [START PATCH] ```java
    <code to be modified here>
``` [END PATCH].

You are also an expert Java programmer and code analyzer.

## Available Tools

You have access to the following Joern-based tools and one Example Patch Search tool:
{tools}
{tool_names}

for each tool call:
  "tool": "Tool to be called from tools, if applicable. Set to 'None' if no tool is needed.",
  "parameters": "The input parameter(s) used by the tools. Only the parameter value(s) (e.g. getIndexOf), (e.g. getLegendItems, AbstractCategoryItemRenderer.java). Set to 'None' if there are no parameters.". DO NOT try to modify any parameter format(e.g., open("Chart-1_buggy") rather than open("Chart_1_buggy"\n") etc.).

- IMPORTANT: Do not modify tool parameters in any way. Use them exactly as provided in the input context. DO NOT Add any quota marks to the parameter because the tool call will process it.

## Analysis Process (Coordinator)

1. Open the CPG for the buggy code (always required as first stage).
2. Analyze the buggy code, focusing on the area marked with /* bug is here */.
3. Use tools to gather necessary information about:
   - Variable definitions and usages
   - Function calls and definitions
   - Control flow
   - Similar patterns in the codebase
   - Import statements and library usage
4. Synthesize gathered information and identify the *root cause* of the defect.
5. Close the project (always required after gathering enough information and before outputting the Summary Answer).
6. Output the Summary Answer.


## Guidelines
- Open and close the CPG project properly.
- Focus on the buggy function and lines (before or at) marked with /* bug is here */.
- Avoid unnecessary tool calls if information can be inferred from context.
- Ensure your analysis covers context, potential causes, and fix suggestions.
- Gather repair contexts as much as you need to draw the root cause.
- Call example_patch_search_tool if you think the Joern analysis information is not enough to provide the Final Answer.
- As the **Coordinator**, your Final Answer must clearly show:
  - One detailed Root Cause.
  - At least three independent Suggestions (three different directions to fix).

## Input Context

- Buggy ID(proj_name): {buggy_id}
- Buggy File: {buggy_file_path}
- Buggy Function:
```java
{buggy_code}
```
- Trigger Test: {trigger_src}
- Error Message: {err_msg}

To help you better use example_patch_search_tool call, make sure the parameter is the full buggy code concatenated with the root cause, here is an Example:
Action Input:"input_string":  <Buggy code>, Root Cause: <content>"

## Output Format (Coordinator)

Use the following format for your analysis:

Thought: [Your reasoning about the next step]
Action: [Tool name]
Action Input: "value1", "value2", ..., if possible, "value N"
Observation: [Tool output]
... (Repeat Thought/Action/Action Input/Observation as needed)
Thought: I now have enough information to provide the Summary Answer
Action: [No Action Needed]
Action Input: "buggy_code, root_cause"

IMPORTANT:
- Once you think you have enough information to answer, you MUST NOT call any tools.
- Instead, your FINAL message must follow the exact format below and must start with "Final Answer:". 

Final Answer(IMPORTANT: Strictly follow the output format below and DO NOT add '#', '*' or other signs before each Root Cause & Suggestion title):
Root Cause: [Detailed explanation of the bug's root cause]

Suggestion 1: [Suggestion title]
[Detailed description of the first repair suggestion, give step by step and sufficient suggestion to help generate correct patches. Only provide how to generate correct patch suggestions, not involve test process. Note that each suggestion should be independent]
Suggestion 2: [Suggestion title]
[Detailed description of the second repair suggestion, give step by step and sufficient suggestion to help generate correct patches. Only provide how to generate correct patch suggestions, not involve test process. Note that each suggestion should be independent]
Suggestion 3: [Suggestion title]
[Detailed description of the third repair suggestion, give step by step and sufficient suggestion to help generate correct patches. Only provide how to generate correct patch suggestions, not involve test process. Note that each suggestion should be independent]

[Add more suggestions as needed, give as much as it can help to generate correct patches. Please make sure your Final Answer follows the format above exactly, without adding any extra marks or symbols]

## Begin Your Analysis (Coordinator)

Start by opening the CPG and analyzing the buggy code. Focus on the area marked with /* bug is here */ and use the available tools to gather necessary information.
Exit the chain when you already output the Final Answer.
Thought: {agent_scratchpad}
"""
)


custodian_prompt_template = PromptTemplate.from_template(
    """
You are acting as the **Custodian** in a multi-agent software repair team.

The Coordinator has already:
- Analyzed the defect.
- Identified the root cause.
- Proposed several high-level directions (Suggestions) to fix the bug.

Your responsibilities as the Custodian:
- Use the Coordinator's analysis and suggestions.
- Use the available tools to **retrieve internal components** from the codebase
  that can help implement these fix directions.
- Internal components include: existing methods, helper utilities, classes,
  configuration patterns, or similar code that can be reused or adapted.

Focus only on **retrieval and identification**, not on designing new fixes.

## Available Tools

You have access to the following Joern-based tools and one Example Patch Search tool:
{tools}
{tool_names}

Use these tools to:
- Locate functions/classes related to the buggy area.
- Identify similar correct implementations elsewhere in the project.
- Find reusable patterns or utility methods.

## Input Context

- Buggy ID(proj_name): {buggy_id}
- Buggy File: {buggy_file_path}
- Buggy Function:
```java
{buggy_code}
```
- Trigger Test: {trigger_src}
- Error Message: {err_msg}

- Coordinator Final Answer:
{manager_final_answer}

## Guidelines (Custodian)

- You MUST use tools where appropriate to ground your findings in actual code.
- Focus on components that align with the Coordinator's suggestions/directions.
- DO NOT propose new fix strategies; only retrieve and describe existing components.
- DO NOT modify tool parameters. Use them exactly as expected.
- Once you think you have enough information to answer, you can generate the Final Output and quit.

Thought: [Your reasoning about the next step]
Action: [Tool name]
Action Input: "value1", "value2" 
Observation: [Tool output]
... (Repeat Thought/Action/Action Input/Observation as needed)
Thought: I now have enough information to provide the Summary Answer.
Action: [No Action Needed]
Action Input: "bug_id, components"

IMPORTANT:
- Once you think you have enough information to answer, you MUST NOT call any tools.
- You MUST NOT output JSON unless you are finishing.
- Instead, your FINAL message must follow the exact format below and must start with "Final Answer:". 

## Final Answer (Custodian) [IMPORTANT: Strictly follow the output format below, Do not include any explanatory text before or after the JSON.
Only output the JSON object.]:

{{
  "bug_id": "<buggy_id>",
  "components": [
    {{
      "name": "<component_name_or_identifier>",
      "location": "<file path or class name>",
      "description": "<how this component can help with the fix>",
      "related_direction": "<which Coordinator suggestion or direction this supports>"
    }}
    // ... more components
  ]
}}



## Begin Your Analysis (Custodian)

Start by reading the Coordinator's Final Answer, then use tools to discover and list useful internal components.
Thought: {agent_scratchpad}
"""
)


class Logger(object):
    """
    Logger that writes messages both to the terminal and a file with
    timestamps and log levels. This mirrors the behavior of the original
    script's logging facility.
    """
    def __init__(self, filename='default.log', stream=sys.stdout):
        self.terminal = stream
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self.log = open(filename, 'a', encoding='utf-8')
        self._closed = False

    def _log(self, level: str, message: str) -> None:
        if message.strip():
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            formatted = f'[{timestamp}] [{level.upper()}] {message}\n'
            self.terminal.write(formatted)
            self.log.write(formatted)

    def info(self, message: str) -> None:
        self._log('info', message)

    def error(self, message: str) -> None:
        self._log('error', message)

    def warning(self, message: str) -> None:
        self._log('warning', message)

    # These methods ensure compatibility with print redirection
    def write(self, message: str) -> None:
        if message.strip():
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            message = f'[{timestamp}] {message}'
        self.terminal.write(message)
        self.log.write(message)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()

    def close(self) -> None:
        self.flush()
        self.log.close()


bug_list = ['Chart-1', 'Chart-10', 'Chart-11', 'Chart-12', 'Chart-13', 'Chart-17', 'Chart-20', 'Chart-23', 'Chart-24', 'Chart-26', 'Chart-3', 'Chart-4', 'Chart-5', 'Chart-6', 'Chart-7', 'Chart-8', 'Chart-9',
    'Closure-1', 'Closure-10', 'Closure-101', 'Closure-102', 'Closure-104', 'Closure-105', 'Closure-107', 'Closure-109', 'Closure-11', 'Closure-111', 'Closure-112', 'Closure-113', 'Closure-114',
    'Closure-115', 'Closure-116', 'Closure-117', 'Closure-118', 'Closure-119', 'Closure-12', 'Closure-120', 'Closure-121', 'Closure-122', 'Closure-123', 'Closure-124', 'Closure-125', 'Closure-126',
   'Closure-127', 'Closure-128', 'Closure-129', 'Closure-13', 'Closure-130', 'Closure-131', 'Closure-132', 'Closure-133', 'Closure-14', 'Closure-15', 'Closure-17', 'Closure-18', 'Closure-19',
    'Closure-2', 'Closure-20', 'Closure-21', 'Closure-22', 'Closure-23', 'Closure-24', 'Closure-25', 'Closure-28', 'Closure-29', 'Closure-31', 'Closure-32', 'Closure-33', 'Closure-35', 'Closure-36',
    'Closure-38', 'Closure-39', 'Closure-40', 'Closure-42', 'Closure-44', 'Closure-48', 'Closure-5', 'Closure-50', 'Closure-51', 'Closure-52', 'Closure-53', 'Closure-55', 'Closure-56', 'Closure-57',
    'Closure-58', 'Closure-59', 'Closure-61', 'Closure-62', 'Closure-65', 'Closure-66', 'Closure-67', 'Closure-69', 'Closure-7', 'Closure-70', 'Closure-71', 'Closure-73', 'Closure-77', 'Closure-78',
    'Closure-8', 'Closure-81', 'Closure-82', 'Closure-83', 'Closure-86', 'Closure-87', 'Closure-88', 'Closure-91', 'Closure-92', 'Closure-94', 'Closure-95', 'Closure-96', 'Closure-97', 'Closure-99',
    'Lang-1', 'Lang-10', 'Lang-11', 'Lang-12', 'Lang-14', 'Lang-16', 'Lang-17', 'Lang-18', 'Lang-19', 'Lang-21', 'Lang-22', 'Lang-24', 'Lang-26', 'Lang-27', 'Lang-28', 'Lang-29', 'Lang-3',
    'Lang-31', 'Lang-33', 'Lang-37', 'Lang-38', 'Lang-39', 'Lang-4', 'Lang-40', 'Lang-42', 'Lang-43', 'Lang-44', 'Lang-45', 'Lang-48', 'Lang-49', 'Lang-5', 'Lang-51', 'Lang-52', 'Lang-53',
    'Lang-54', 'Lang-55', 'Lang-57', 'Lang-58', 'Lang-59', 'Lang-6', 'Lang-61', 'Lang-65', 'Lang-9', 'Math-10', 'Math-101', 'Math-102', 'Math-103', 'Math-104', 'Math-105', 'Math-106', 'Math-11',
    'Math-13', 'Math-15', 'Math-16', 'Math-17', 'Math-19', 'Math-2', 'Math-20', 'Math-21', 'Math-23', 'Math-24', 'Math-25', 'Math-26', 'Math-27', 'Math-28', 'Math-3', 'Math-30', 'Math-31',
    'Math-32', 'Math-33', 'Math-34', 'Math-38', 'Math-39', 'Math-40', 'Math-41', 'Math-42', 'Math-43', 'Math-44', 'Math-45', 'Math-48', 'Math-5', 'Math-50', 'Math-51', 'Math-52', 'Math-53',
    'Math-55', 'Math-56', 'Math-57', 'Math-58', 'Math-59', 'Math-60', 'Math-61', 'Math-63', 'Math-64', 'Math-69', 'Math-7', 'Math-70', 'Math-72', 'Math-73', 'Math-74', 'Math-75', 'Math-78',
    'Math-79', 'Math-8', 'Math-80', 'Math-82', 'Math-84', 'Math-85', 'Math-86', 'Math-87', 'Math-88', 'Math-89', 'Math-9', 'Math-90', 'Math-91', 'Math-94', 'Math-95', 'Math-96', 'Math-97',
    'Mockito-1', 'Mockito-12', 'Mockito-13', 'Mockito-15', 'Mockito-18', 'Mockito-2', 'Mockito-20', 'Mockito-22', 'Mockito-24', 'Mockito-26', 'Mockito-27', 'Mockito-28', 'Mockito-29', 'Mockito-3',
    'Mockito-31', 'Mockito-32', 'Mockito-33', 'Mockito-34', 'Mockito-36', 'Mockito-37', 'Mockito-38', 'Mockito-5', 'Mockito-7', 'Mockito-8', 'Mockito-9', 'Time-10', 'Time-14', 'Time-15', 'Time-16',
   'Time-17', 'Time-18', 'Time-19', 'Time-20', 'Time-22', 'Time-23', 'Time-24', 'Time-25', 'Time-27', 'Time-4', 'Time-5', 'Time-7', 'Time-8', 'Time-9'
]


with open("/MAS4APR/D4J_dataset/defects4j-sf.json", 'r') as f:
    data = json.load(f)
 
# Keep a reference to the original stdout so we can restore it after each bug.
original_stdout = sys.stdout

for name in bug_list:
    logger = Logger('/MAS4APR/src/output/log/' + name + '.log', sys.stdout)
    sys.stdout = logger
    try:
        i = 1
        current_time: Union[datetime, None] = None
        start_time = datetime.now()
        total_cost = 0

        # -----------------------------
        # Solution generation phase
        # -----------------------------
        while i < 2:
            start_time_ite = datetime.now()
            print(f"Generating Solution for: {name}, Round: {i}")
            i += 1

            trigger_test_list = list(data[name]['trigger_test'].keys())
            trigger_src_list: list = []
            err_msg_list: list = []
            for trigger_test in trigger_test_list:
                trigger_src_list.append(data[name]['trigger_test'][trigger_test]['src'])
                err_msg_list.append(data[name]['trigger_test'][trigger_test]['clean_error_msg'])

            # Coordinator agent
            manager_prompt = manager_prompt_template.partial(
                buggy_id=name + "_buggy",
                buggy_file_path=data[name]['loc'],
                buggy_code=data[name]['buggy_fl'],
                trigger_src=str(trigger_src_list),
                err_msg=str(err_msg_list),
                tools=tool_descriptions,
                tool_names=tool_names,
            )
            manager_llm = ChatOpenAI(model_name="gpt-4o-2024-05-13")
            manager_llm._default_params.pop("stop", None)
            manager_agent = create_react_agent(manager_llm, tools, manager_prompt)

            try:
                manager_executor = AgentExecutor(
                    agent=manager_agent,
                    tools=tools,
                    verbose=True,
                    stream_runnable=False,
                    handle_parsing_errors=(
                        "Check your output and make sure it conforms, use the Action/Action Input syntax. "
                        "Output the Final Answer always should always required as last step. Exit after output the Final Answer."
                    ),
                )

                with get_openai_callback() as cb:
                    manager_result = manager_executor.invoke({})
                    logger.info(f"[Coordinator] Total tokens: {cb.total_tokens}")
                    logger.info(f"[Coordinator] Prompt tokens: {cb.prompt_tokens}")
                    logger.info(f"[Coordinator] Completion tokens: {cb.completion_tokens}")
                    logger.info(f"[Coordinator] Cost (USD): ${cb.total_cost}")
                    total_cost += cb.total_cost

                logger.info("[Coordinator] Final output:")
                logger.info(manager_result['output'])

                # Save Coordinator final answer for patch generation later
                extract_and_save_final_answer(
                    manager_result['output'],
                    "/MAS4APR/src/output/solutions/" + name + ".json",
                    name,
                )

            except ValueError as e:
                response = str(e)
                if not response.startswith("Could not parse LLM output: `"):
                    raise e
                response = response.removeprefix("Could not parse LLM output: `").removesuffix("`")

            # Custodian agent
            custodian_prompt = custodian_prompt_template.partial(
                buggy_id=name + "_buggy",
                buggy_file_path=data[name]['loc'],
                buggy_code=data[name]['buggy_fl'],
                trigger_src=str(trigger_src_list),
                err_msg=str(err_msg_list),
                manager_final_answer=manager_result['output'],
                tools=tool_descriptions,
                tool_names=tool_names,
            )
            custodian_llm = ChatOpenAI(model_name="gpt-4o-2024-05-13")
            custodian_agent = create_react_agent(custodian_llm, tools, custodian_prompt)

            try:
                custodian_executor = AgentExecutor(
                    agent=custodian_agent,
                    tools=tools,
                    verbose=True,
                    stream_runnable=False,
                    handle_parsing_errors=(
                        "Check your output and make sure it conforms, use the Action/Action Input syntax. "
                        "Finally, output ONLY a valid JSON object as requested."
                    ),
                )

                with get_openai_callback() as cb:
                    custodian_result = custodian_executor.invoke({})
                    logger.info(f"[Custodian] Total tokens: {cb.total_tokens}")
                    logger.info(f"[Custodian] Prompt tokens: {cb.prompt_tokens}")
                    logger.info(f"[Custodian] Completion tokens: {cb.completion_tokens}")
                    logger.info(f"[Custodian] Cost (USD): ${cb.total_cost}")
                    total_cost += cb.total_cost

                logger.info("[Custodian] Final output:")
                logger.info(custodian_result['output'])

                custodian_output_file = (
                    "/MAS4APR/src/output/components/" + name + ".json"
                )
                save_custodian_components(
                    custodian_result['output'],
                    custodian_output_file,
                    name + "_buggy",
                )

            except ValueError as e:
                response = str(e)
                if not response.startswith("Could not parse LLM output: `"):
                    raise e
                response = response.removeprefix("Could not parse LLM output: `").removesuffix("`")

            # Timing logs
            if current_time:
                pre_current = current_time
            else:
                pre_current = start_time
            current_time = datetime.now()
            time_difference = current_time - pre_current
            logger.info(f"Iteration {i - 1} time: {time_difference}")

        # Log total time for generating solutions
        current_time = datetime.now()
        time_difference = current_time - start_time
        logger.info(f"Time cost after iteration: {time_difference}")

        # -----------------------------
        # Patch generation phase
        # -----------------------------
        print("*********** Patch Generation Phase ***********")
        trigger_test_list = list(data[name]['trigger_test'].keys())
        trigger_src_list = []
        err_msg_list = []
        for trigger_test in trigger_test_list:
            trigger_src_list.append(data[name]['trigger_test'][trigger_test]['src'])
            err_msg_list.append(data[name]['trigger_test'][trigger_test]['clean_error_msg'])

        output_patches_file = "/MAS4APR/src/output/sf_patches/" + name + ".json"
        buggy_code = data[name]['buggy_fl']
        context = [
            "buggy_id: " + name + "_buggy",
            "\nbuggy_code: " + buggy_code,
            "\nbuggy_file_path: " + data[name]['loc'],
            "\ntrigger_src: " + str(trigger_src_list),
            "\nerr_msg: " + str(err_msg_list),
        ]

        with open("/MAS4APR/src/output/solutions/" + name + ".json") as f2:
            solutions = json.load(f2)

        SF_COMPONENTS_DIR = "/MAS4APR/src/output/components"
        retrieved_components_context = load_retrieved_components_context(SF_COMPONENTS_DIR, name)
        
        for root_cause in solutions[name].keys():
            for suggestion in solutions[name][root_cause]:
                prompt_patch = f"""You expert Java programmer in generating patches for a given buggy snippet.\n Bug code information(buggy_id, buggy_code, buggy_file_path, trigger_src, err_msg):"+{str(context)}+"\nYour task is to generate ONE unique version of fix for the buggy function code for the pair of root cause and suggestion: Root Cause: {str(root_cause)}\n{str(suggestion)}\n. You should look deeply through all the retrieved internal components related to the buggy codes: (component_name, component_location, description, related_direction)"+{str(retrieved_components_context)}+"\n and combine the root cause and repair suggestions to generate correct patches,
    Requirement:
    1. For each root cause and suggestion pair, generate ONE patch.
    2. Please first consider using the retrieved internal components to help generate the patch.
    3. Each patch should be a complete version of the buggy function code with the fix applied.
    4. Ensure each patch is unique and addresses the identified issue.
    5. When looking at the buggy function code, the location of the bug is indicated by the /* bug is here */ comment.
    6. The fix may involve changes to surrounding lines, not just the commented line itself.

    Return the whole fixed buggy function code, NOT just the line(s) of fixed area. Only output the following content, nothing else. Please format your response as follows:
    [START PATCH 1]
    ```java
    <entire buggy function fixed code here>
    ```
    [END PATCH 1]

                """

                try:
                    response = api_gpt_response(prompt_patch, 5)
                    usage = response.usage
                    if usage:
                        input_cost = usage.prompt_tokens * 0.000005
                        output_cost = usage.completion_tokens * 0.00002
                        patch_total_cost = input_cost + output_cost
                        logger.info(f"Patch prompt tokens: {usage.prompt_tokens}")
                        logger.info(f"Patch completion tokens: {usage.completion_tokens}")
                        logger.info(f"Patch total tokens: {usage.total_tokens}")
                        logger.info(f"Patch total cost $: {patch_total_cost}")
                        total_cost += patch_total_cost
                    else:
                        logger.info("No tokens recorded")
                    print("***********Gen Patch***********")
                    print(response)

                    for choice in response.choices:
                        print("-----------")
                        suggest_patch = []
                        generated_text = choice.message.content.strip()
                        suggest_patch.append(parse_patches(generated_text))
                        logger.info(f"suggest_patch: {parse_patches(generated_text)}")
                        append_patches_to_json(suggest_patch[0], output_patches_file, name)
                except openai.OpenAIError as e:
                    print(f'OpenAI API error: {e}')
                    break

        current_time_2 = datetime.now()
        time_difference_ite = current_time_2 - current_time
        logger.info(f"Time cost for generate patch: {time_difference_ite}")

        end_time = datetime.now()
        time_difference = end_time - start_time
        logger.info(f"Time cost for {name}: {time_difference}")
        logger.info(f"Money cost for {name}: {total_cost}")
    finally:
        sys.stdout = original_stdout
        logger.close()
        time.sleep(10)