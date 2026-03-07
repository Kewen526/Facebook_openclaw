#!/usr/bin/env python3
"""
Patch browser-use's openai/chat.py to handle GLM/Zhipu model output format issues.

GLM models don't strictly follow OpenAI's structured output format:
1. Wrap JSON in markdown code blocks (```json ... ```)
2. Return string values for integer fields (e.g., "Step 4" for current_plan_item)
3. Add extra fields not in the schema (causes 'extra_forbidden' error)
4. Omit required fields like 'action'
5. Nest fields under 'current_state' instead of flat structure
6. Use alternative field names ('actions' instead of 'action')

This patch replaces the simple model_validate_json() call with a robust
transformation pipeline that fixes all these issues.
"""

import os
import sys
import textwrap


def find_chat_py():
    """Find browser_use's openai/chat.py"""
    try:
        import browser_use
        base = os.path.dirname(browser_use.__file__)
        path = os.path.join(base, 'llm', 'openai', 'chat.py')
        if os.path.exists(path):
            return path
    except ImportError:
        pass

    # Fallback: common paths
    for p in [
        '/usr/local/lib/python3.11/dist-packages/browser_use/llm/openai/chat.py',
        '/usr/local/lib/python3.11/site-packages/browser_use/llm/openai/chat.py',
    ]:
        if os.path.exists(p):
            return p

    return None


# The helper function to inject into chat.py
HELPER_CODE = textwrap.dedent('''\
import json as _json
import re as _re


def _transform_model_output(raw_content: str, output_format) -> str:
    """
    Transform raw LLM output into valid JSON matching the output_format schema.
    Handles markdown wrappers, extra fields, missing fields, type mismatches, etc.
    """
    s = raw_content.strip()

    # --- Step 1: Remove markdown code block wrappers ---
    m = _re.search(r'```(?:json)?\\s*\\n?(.*?)```', s, _re.DOTALL)
    if m:
        s = m.group(1).strip()

    # --- Step 2: Remove XML tag wrappers ---
    m2 = _re.search(r'<\\w+>\\s*(.*?)\\s*</\\w+>', s, _re.DOTALL)
    if m2 and m2.group(1).strip().startswith('{'):
        s = m2.group(1).strip()

    # --- Step 3: Extract JSON object if there's leading text ---
    if not s.startswith('{'):
        start = s.find('{')
        end = s.rfind('}')
        if start != -1 and end > start:
            s = s[start:end+1]

    # --- Step 4: Parse JSON ---
    try:
        data = _json.loads(s)
    except _json.JSONDecodeError:
        return s  # Can't parse; return as-is and let Pydantic report the error

    if not isinstance(data, dict):
        return s

    # --- Step 5: Get valid fields from the schema ---
    try:
        schema = output_format.model_json_schema()
        valid_fields = set(schema.get('properties', {}).keys())
    except Exception:
        valid_fields = {
            'thinking', 'evaluation_previous_goal', 'memory',
            'next_goal', 'current_plan_item', 'plan_update', 'action'
        }

    # --- Step 6: Flatten nested 'current_state' structure ---
    if 'current_state' in data and isinstance(data['current_state'], dict):
        cs = data.pop('current_state')
        for k, v in cs.items():
            if k not in data:
                data[k] = v

    # --- Step 7: Map alternative field names to canonical names ---
    field_mappings = {
        'eval': 'evaluation_previous_goal',
        'evaluation': 'evaluation_previous_goal',
        'prev_goal_eval': 'evaluation_previous_goal',
        'previous_goal_evaluation': 'evaluation_previous_goal',
        'goal': 'next_goal',
        'next': 'next_goal',
        'actions': 'action',
        'plan': 'plan_update',
        'memo': 'memory',
        'thought': 'thinking',
    }
    for alt, canonical in field_mappings.items():
        if alt in data and canonical not in data:
            data[canonical] = data.pop(alt)

    # --- Step 8: Remove extra fields (causes 'extra_forbidden' error) ---
    cleaned = {}
    for k, v in data.items():
        if k in valid_fields:
            cleaned[k] = v

    # --- Step 9: Fix current_plan_item type (string -> int) ---
    if 'current_plan_item' in cleaned:
        v = cleaned['current_plan_item']
        if isinstance(v, str):
            nums = _re.findall(r'\\d+', v)
            cleaned['current_plan_item'] = int(nums[0]) if nums else None
        elif v is not None and not isinstance(v, int):
            cleaned['current_plan_item'] = None

    # --- Step 10: Ensure required fields have sensible defaults ---
    if 'evaluation_previous_goal' in valid_fields and 'evaluation_previous_goal' not in cleaned:
        cleaned['evaluation_previous_goal'] = 'Unknown - no evaluation provided by model.'

    if 'memory' in valid_fields and 'memory' not in cleaned:
        cleaned['memory'] = 'No memory state available.'

    if 'next_goal' in valid_fields and 'next_goal' not in cleaned:
        cleaned['next_goal'] = 'Determine the appropriate next action.'

    # --- Step 11: Ensure action field exists ---
    if 'action' not in cleaned:
        cleaned['action'] = []
    elif not isinstance(cleaned['action'], list):
        cleaned['action'] = [cleaned['action']]

    # --- Step 12: Ensure plan_update is a list if present ---
    if 'plan_update' in cleaned and not isinstance(cleaned['plan_update'], list):
        if isinstance(cleaned['plan_update'], str):
            cleaned['plan_update'] = [cleaned['plan_update']]
        else:
            cleaned['plan_update'] = []

    return _json.dumps(cleaned, ensure_ascii=False)

''')


def apply_patch(chat_py_path):
    """Apply the patch to chat.py"""
    with open(chat_py_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if already patched with our new version
    if '_transform_model_output' in content:
        print(f'[INFO] Already patched with _transform_model_output: {chat_py_path}')
        return True

    # Remove old patch if present
    if '_clean_json_wrapper' in content:
        print('[INFO] Removing old _clean_json_wrapper patch...')
        # Remove the old function definition
        # Find from 'import re as _re' to end of _clean_json_wrapper function
        old_start = content.find('import re as _re')
        if old_start != -1:
            # Find the end of _clean_json_wrapper function
            func_end = content.find('\n\n', content.find('def _clean_json_wrapper'))
            if func_end != -1:
                content = content[:old_start] + content[func_end+2:]

        # Revert the parse line if it was changed
        content = content.replace(
            'output_format.model_validate_json(_clean_json_wrapper(choice.message.content))',
            'output_format.model_validate_json(choice.message.content)'
        )

    # --- Insert the helper function after imports ---
    # Find a good insertion point (after the last import, before the class definition)
    insert_marker = "T = TypeVar('T', bound=BaseModel)"
    insert_pos = content.find(insert_marker)
    if insert_pos == -1:
        print('[ERROR] Could not find insertion point in chat.py')
        return False

    insert_pos = content.find('\n', insert_pos) + 1  # After the line
    content = content[:insert_pos] + '\n' + HELPER_CODE + '\n' + content[insert_pos:]

    # --- Replace the parse line ---
    old_parse = 'parsed = output_format.model_validate_json(choice.message.content)'
    new_parse = 'parsed = output_format.model_validate_json(_transform_model_output(choice.message.content, output_format))'

    if old_parse not in content:
        print('[ERROR] Could not find the model_validate_json line to patch')
        return False

    content = content.replace(old_parse, new_parse)

    # --- Write the patched file ---
    # Backup first
    backup_path = chat_py_path + '.bak'
    if not os.path.exists(backup_path):
        import shutil
        shutil.copy2(chat_py_path, backup_path)
        print(f'[INFO] Backup saved to: {backup_path}')

    with open(chat_py_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f'[OK] Patch applied successfully to: {chat_py_path}')
    return True


def main():
    chat_py = find_chat_py()
    if not chat_py:
        print('[ERROR] Could not find browser_use/llm/openai/chat.py')
        sys.exit(1)

    print(f'[INFO] Found chat.py at: {chat_py}')

    if apply_patch(chat_py):
        print('[OK] All done! Restart your server to apply changes.')
    else:
        print('[ERROR] Patch failed.')
        sys.exit(1)


if __name__ == '__main__':
    main()
