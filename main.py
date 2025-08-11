# main.py

import sys
import re
from typing import Tuple, Dict, Any

# --- Global Storage for Blueprints ---
FUNCTIONS: Dict[str, dict] = {}
CLASSES: Dict[str, dict] = {}

# --- Debugging ---
DEBUG = False
def _dbg(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def strip_inline_comments(line: str) -> str:
    """
    Removes all inline comments in the format { ... } unless preceded by $.
    """
    pattern = r'(?<!\$)\{.*?\}'
    return re.sub(pattern, '', line)

def substitute_vars(line: str, variables: dict) -> str:
    """
    Recursively finds and replaces all ${...} expressions in a line.
    """
    pattern = r'\$\{([^{}]*?)\}'
    while (match := re.search(pattern, line)):
        expression = match.group(1)
        value = safe_eval(expression, variables)
        if value is not None:
            # Use str() so strings don't get extra quotes in output
            replacement = str(value)
            line = line[:match.start()] + replacement + line[match.end():]
        else:
            print(f"Warning: Could not evaluate nested expression '${expression}'.")
            return line
    return line

def safe_eval(expression: str, variables: dict):
    """
    Safely evaluates an MSlash expression.
    """
    allowed_builtins = {
        "True": True,
        "False": False,
        "None": None,
        "str": str,
        "int": int,
        "float": float,
        "list": list,
        "dict": dict,
        "len": len,
        "type": type,
    }
    eval_globals = {"__builtins__": allowed_builtins}

    # Inject this.attributes as locals; keep 'this' for attribute-style access
    eval_locals = variables.copy()
    if 'this' in eval_locals and isinstance(eval_locals['this'], MSlashObject):
        eval_locals.update(eval_locals['this'].attributes)

    try:
        return eval(expression, eval_globals, eval_locals)
    except Exception:
        return None

class MSlashObject:
    """Represents an instance of an MSlash class."""
    def __init__(self, class_name: str):
        # Avoid recursion in __setattr__
        super().__setattr__('class_name', class_name)
        super().__setattr__('attributes', {})

    def __repr__(self):
        return f"<instance of {self.class_name}>"

    # Allow attribute-style reads: this.foo -> attributes["foo"]
    def __getattr__(self, name: str):
        attrs = super().__getattribute__('attributes')
        if name in attrs:
            return attrs[name]
        raise AttributeError(name)

    # Optional: if Python eval ever does "this.x = y", route it to attributes
    def __setattr__(self, name: str, value: Any):
        if name in ('class_name', 'attributes'):
            super().__setattr__(name, value)
        else:
            self.attributes[name] = value

def execute(lines, variables):
    """
    Executes a list of MSlash script lines.
    Returns a value if a 'return' statement is hit.
    """
    pc = 0
    while pc < len(lines):
        raw_line = lines[pc]
        line_no_comments = strip_inline_comments(raw_line)
        original_line = line_no_comments.strip()

        if not original_line:
            pc += 1
            continue

        _dbg(f"L{pc+1} RAW: {raw_line.rstrip()}")
        _dbg(f"L{pc+1} PARSED: {original_line}")

        # --- Patterns ---
        func_stmt_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\((.*)\)$', original_line)
        method_stmt_match = re.match(r'^(.*)\.([a-zA-Z_][a-zA-Z0-9_]*)\((.*)\)$', original_line)

        # --- Module Import: steal <symbol> from <file>.mslash ---
        if original_line.startswith("steal "):
            steal_match = re.match(r'^steal\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+from\s+([^\s]+)$', original_line)
            if not steal_match:
                print(f"Syntax Error on line {pc+1}: Invalid steal syntax.")
            else:
                symbol = steal_match.group(1)
                module_path = steal_match.group(2)
                try:
                    mod_vars, mod_funcs, mod_classes = load_module(module_path)
                    if symbol in mod_vars:
                        variables[symbol] = mod_vars[symbol]
                        _dbg(f"STEAL var {symbol} from {module_path}")
                    elif symbol in mod_funcs:
                        FUNCTIONS[symbol] = mod_funcs[symbol]
                        _dbg(f"STEAL func {symbol} from {module_path}")
                    elif symbol in mod_classes:
                        CLASSES[symbol] = mod_classes[symbol]
                        _dbg(f"STEAL class {symbol} from {module_path}")
                    else:
                        print(f"Import Error on line {pc+1}: '{symbol}' not found in {module_path}.")
                except FileNotFoundError:
                    print(f"Import Error on line {pc+1}: Module file '{module_path}' not found.")
                except Exception as e:
                    print(f"Import Error on line {pc+1}: {e}")

        # --- Global Function Call (statement) ---
        elif func_stmt_match and func_stmt_match.group(1) in FUNCTIONS:
            func_name = func_stmt_match.group(1)
            arg_str = func_stmt_match.group(2)
            func_info = FUNCTIONS[func_name]
            arg_names = func_info['args']

            arg_values_raw = [v.strip() for v in arg_str.split(',')] if arg_str else []
            if len(arg_values_raw) != len(arg_names):
                print(f"Error: Function '{func_name}' expects {len(arg_names)} arguments, but got {len(arg_values_raw)}.")
            else:
                arg_values = [safe_eval(substitute_vars(v, variables), variables) for v in arg_values_raw]
                local_vars = dict(zip(arg_names, arg_values))
                _dbg(f"CALL func {func_name}({', '.join(map(str, arg_values))})")
                _ = execute(func_info['body'], local_vars)  # side-effects only

        # --- Object Instantiation ---
        elif original_line.startswith("var ") and "new " in original_line:
            try:
                name_part, value_part = original_line[4:].split('=', 1)
                name = name_part.strip()

                match = re.match(r'new\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)', value_part.strip())
                if match:
                    class_name = match.group(1)
                    arg_str = match.group(2)

                    if class_name in CLASSES:
                        new_object = MSlashObject(class_name)

                        if 'init' in CLASSES[class_name]['methods']:
                            init_func = CLASSES[class_name]['methods']['init']
                            arg_names = init_func['args']
                            arg_values_raw = [v.strip() for v in arg_str.split(',')] if arg_str else []
                            arg_values = [safe_eval(substitute_vars(v, variables), variables) for v in arg_values_raw]

                            local_vars = {'this': new_object}
                            local_vars.update(dict(zip(arg_names, arg_values)))

                            _dbg(f"NEW {class_name}({', '.join(map(str, arg_values))}) as {name} -> calling init")
                            execute(init_func['body'], local_vars)

                        variables[name] = new_object
                        _dbg(f"SET var {name} = <instance of {class_name}>")
                    else:
                        print(f"Error: Class '{class_name}' is not defined.")
                else:
                    print(f"Syntax Error: Invalid 'new' statement.")
            except ValueError:
                print(f"Syntax Error on line {pc + 1}: Invalid variable assignment.")

        # --- Method Call (statement) ---
        elif method_stmt_match:
            obj_expr = method_stmt_match.group(1)
            method_name = method_stmt_match.group(2)
            arg_str = method_stmt_match.group(3)

            obj_instance = safe_eval(substitute_vars(obj_expr, variables), variables)

            if isinstance(obj_instance, MSlashObject):
                class_info = CLASSES.get(obj_instance.class_name)
                if class_info and method_name in class_info['methods']:
                    method_info = class_info['methods'][method_name]
                    arg_names = method_info['args']

                    arg_values_raw = [v.strip() for v in arg_str.split(',')] if arg_str else []
                    arg_values = [safe_eval(substitute_vars(v, variables), variables) for v in arg_values_raw]

                    local_vars = {'this': obj_instance}
                    local_vars.update(dict(zip(arg_names, arg_values)))

                    _dbg(f"CALL {obj_expr}.{method_name}({', '.join(map(str, arg_values))})")
                    ret = execute(method_info['body'], local_vars)
                    _dbg(f"RET {obj_expr}.{method_name} -> {ret}")
                else:
                    print(f"Error: Method '{method_name}' not found on object.")
            else:
                print(f"Error: '{obj_expr}' did not evaluate to an object.")

        elif original_line.startswith("var "):
            # Instance attribute set
            if original_line.startswith("var this."):
                if 'this' in variables and isinstance(variables['this'], MSlashObject):
                    try:
                        _, assign_part = original_line.split("var this.", 1)
                        attr_name, value_str = assign_part.split('=', 1)
                        attr_name = attr_name.strip()
                        value = safe_eval(substitute_vars(value_str.strip(), variables), variables)
                        variables['this'].attributes[attr_name] = value
                        _dbg(f"SET this.{attr_name} = {value}")
                    except ValueError:
                        print(f"Syntax Error on line {pc+1}: Invalid attribute assignment.")
                else:
                    print(f"Error: 'this' can only be used inside a class method.")

            else:
                # Regular variable assignment (including assign-from-function-call)
                try:
                    name_part, value_part = original_line[4:].split('=', 1)
                    name = name_part.strip()
                    value_str = value_part.strip()

                    # var x = foo(a, b)
                    var_call_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\((.*)\)$', value_str)
                    if var_call_match and var_call_match.group(1) in FUNCTIONS:
                        func_name = var_call_match.group(1)
                        arg_str = var_call_match.group(2)
                        func_info = FUNCTIONS[func_name]
                        arg_names = func_info['args']
                        arg_values_raw = [v.strip() for v in arg_str.split(',')] if arg_str else []

                        if len(arg_values_raw) != len(arg_names):
                            print(f"Error: Function '{func_name}' expects {len(arg_names)} arguments, but got {len(arg_values_raw)}.")
                            variables[name] = None
                        else:
                            arg_values = [safe_eval(substitute_vars(v, variables), variables) for v in arg_values_raw]
                            local_vars = dict(zip(arg_names, arg_values))
                            _dbg(f"CALL func {func_name}({', '.join(map(str, arg_values))}) for assignment to {name}")
                            return_value = execute(func_info['body'], local_vars)
                            variables[name] = return_value
                            _dbg(f"SET var {name} = {return_value}")

                    else:
                        # Regular assignment with interpolation; Vata dict literal via ()
                        value_str = substitute_vars(value_str, variables)
                        if value_str.startswith('(') and value_str.endswith(')'):
                            value_str = '{' + value_str[1:-1] + '}'

                        result = safe_eval(value_str, variables)
                        if result is not None:
                            variables[name] = result
                            _dbg(f"SET var {name} = {result}")
                        else:
                            print(f"Syntax Error or invalid value for variable '{name}' on line {pc+1}.")
                except ValueError:
                    print(f"Syntax Error on line {pc + 1}: Invalid variable assignment.")

        elif original_line.startswith("say "):
            content_to_eval = original_line[4:].strip()
            substituted_content = substitute_vars(content_to_eval, variables)
            final_output = safe_eval(substituted_content, variables)
            if final_output is not None:
                print(final_output)
            else:
                print(substituted_content)

        elif original_line.startswith("input "):
            var_name = original_line.split()[1].strip()
            user_input = input()
            try:
                parsed_input = eval(user_input, {"__builtins__": None})
                if isinstance(parsed_input, (int, float)):
                    variables[var_name] = parsed_input
                else:
                    variables[var_name] = user_input
            except Exception:
                variables[var_name] = user_input
            _dbg(f"INPUT -> {var_name} = {variables[var_name]}")

        elif original_line.startswith("math "):
            line = substitute_vars(original_line, variables)
            result = safe_eval(line[5:], variables)
            if result is not None:
                print(result)

        elif original_line.startswith("emptyline "):
            line = substitute_vars(original_line, variables)
            try:
                num_lines = int(line[10:].strip())
                if num_lines > 0:
                    print("\n" * (num_lines - 1), end="")
            except (ValueError, IndexError):
                print(f"Invalid number for emptyline on line {pc + 1}")

        elif original_line == "pause":
            input("Press Enter to continue...")

        elif original_line == "break":
            print("--- Script terminated by break ---")
            sys.exit()

        elif original_line.startswith("return "):
            expression = original_line[7:].strip()
            return_value = safe_eval(substitute_vars(expression, variables), variables)
            _dbg(f"RETURN {return_value}")
            return return_value

        elif original_line.startswith("if "):
            line = substitute_vars(original_line, variables)
            condition_str = line[3:]
            result = safe_eval(condition_str, variables)

            if_block_start = pc + 1
            else_pos = -1
            endif_pos = -1
            nest_level = 0

            for i in range(if_block_start, len(lines)):
                scan_line = strip_inline_comments(lines[i]).strip()
                if scan_line.startswith(("if ", "loop ", "func ", "class ")):
                    nest_level += 1
                elif scan_line in ["endif", "endloop", "endfunc", "endclass"]:
                    if nest_level == 0:
                        endif_pos = i
                        break
                    else:
                        nest_level -= 1
                elif scan_line == "else" and nest_level == 0:
                    else_pos = i

            if endif_pos == -1:
                print(f"Syntax Error: 'if' on line {pc + 1} has no matching 'endif'.")
                break

            _dbg(f"IF {condition_str} -> {result}")
            if result:
                if_block_end = else_pos if else_pos != -1 else endif_pos
                execute(lines[if_block_start:if_block_end], variables)
            else:
                if else_pos != -1:
                    execute(lines[else_pos + 1:endif_pos], variables)

            pc = endif_pos

        elif original_line.startswith("loop "):
            line = substitute_vars(original_line, variables)
            try:
                times = int(line.split()[1])
            except (ValueError, IndexError):
                print(f"Syntax Error on line {pc + 1}: Invalid loop syntax.")
                pc += 1
                continue

            loop_body_start = pc + 1
            loop_body_end = -1
            nest_level = 0
            for i in range(loop_body_start, len(lines)):
                scan_line = strip_inline_comments(lines[i]).strip()
                if scan_line.startswith(("if ", "loop ", "func ", "class ")):
                    nest_level += 1
                elif scan_line in ["endif", "endloop", "endfunc", "endclass"]:
                    if nest_level == 0:
                        loop_body_end = i
                        break
                    else:
                        nest_level -= 1

            if loop_body_end == -1:
                print(f"Syntax Error: 'loop' on line {pc + 1}: No matching 'endloop'.")
                break

            loop_code = lines[loop_body_start:loop_body_end]
            _dbg(f"LOOP {times}x (body lines {loop_body_start+1}-{loop_body_end})")
            for _ in range(times):
                execute(loop_code, variables.copy())
            pc = loop_body_end

        elif original_line == "help":
            print("--- MSlash Help ---")
            print("steal <symbol> from <file>.mslash - Import a variable/function/class from another file.")
            print("my_func(args)            - Calls a global function.")
            print("class <name> / endclass  - Defines a class.")
            print("func <name>(args) / endfunc - Defines a function or method.")
            print("return <value>           - Returns a value from a function/method.")
            print("{ comment }              - An inline comment.")
            print("var <name> = <value>     - Assigns a value. Supports var x = myFunc(...).")
            print("say <message>            - Prints a message to the console.")
            print("input <var_name>         - Prompts for user input.")
            print("math <expression>        - Evaluates a mathematical expression.")
            print("if <condition>           - Starts a conditional block.")
            print("else / endif             - Used for conditional logic.")
            print("loop <number> / endloop  - Starts a loop block.")
            print("emptyline <number>       - Prints a number of empty lines.")
            print("pause                    - Pauses execution until Enter is pressed.")
            print("break                    - Terminates the script immediately.")
            print("\n--- Data Types ---")
            print("List: [item1, item2, ...]")
            print("Vata (Dictionary): (\"key1\":\"value1\", ...)")
            print("---------------------")

        elif original_line not in ["else", "endif", "endloop", "endfunc", "endclass"]:
            print(f"Unknown command or syntax error on line {pc + 1}: '{original_line}'")

        pc += 1
    return None

def preprocess_script(all_lines):
    """
    First pass over the script to find all class and function definitions.
    """
    main_code = []
    in_construct = None
    construct_name = None
    construct_body = []
    nest_level = 0
    current_class_name = None

    i = 0
    while i < len(all_lines):
        line = all_lines[i]
        clean_line = strip_inline_comments(line).strip()

        if clean_line.startswith("class "):
            if not in_construct:
                in_construct = 'class'
                current_class_name = clean_line.split()[1]
                construct_body = []
                nest_level = 0
            else:
                construct_body.append(line)
                nest_level += 1

        elif clean_line.startswith("func "):
            if not in_construct:
                in_construct = 'func'
                match = re.match(r'func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)', clean_line)
                if match:
                    construct_name = match.group(1)
                    arg_str = match.group(2)
                    func_args = [arg.strip() for arg in arg_str.split(',')] if arg_str else []
                    FUNCTIONS[construct_name] = {'args': func_args, 'body': []}
                construct_body = []
                nest_level = 0
            else:
                construct_body.append(line)
                nest_level += 1

        elif clean_line.startswith("endclass"):
            if in_construct == 'class' and nest_level == 0:
                preprocess_class_body(current_class_name, construct_body)
                in_construct = None
                construct_body = []
                current_class_name = None
            elif in_construct:
                construct_body.append(line)
                nest_level -= 1
            else:
                print(f"Syntax Error: 'endclass' without matching 'class'.")

        elif clean_line == "endfunc":
            if in_construct and nest_level == 0:
                if in_construct == 'func':
                    FUNCTIONS[construct_name]['body'] = construct_body
                in_construct = None
                construct_body = []
            elif in_construct:
                construct_body.append(line)
                nest_level -= 1
            else:
                print(f"Syntax Error: 'endfunc' without matching 'func'.")

        elif in_construct:
            construct_body.append(line)
            if clean_line.startswith(("class ", "func ", "if ", "loop ")):
                nest_level += 1
            elif clean_line in ["endclass", "endfunc", "endif", "endloop"]:
                if nest_level > 0:
                    nest_level -= 1
        else:
            main_code.append(line)

        i += 1

    return main_code

def preprocess_class_body(class_name, class_lines):
    """Parses the body of a class to find its methods."""
    methods = {}
    in_method = False
    method_name = None
    method_args = []
    method_body = []
    nest_level = 0

    for line in class_lines:
        clean_line = strip_inline_comments(line).strip()
        if clean_line.startswith("func "):
            if not in_method:
                in_method = True
                match = re.match(r'func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)', clean_line)
                if match:
                    method_name = match.group(1)
                    arg_str = match.group(2)
                    method_args = [arg.strip() for arg in arg_str.split(',')] if arg_str else []
                method_body = []
                nest_level = 0
            else:
                method_body.append(line)
                nest_level += 1

        elif clean_line == "endfunc":
            if in_method and nest_level == 0:
                methods[method_name] = {'args': method_args, 'body': method_body}
                in_method = False
                method_body = []
            elif in_method:
                method_body.append(line)
                nest_level -= 1

        elif in_method:
            method_body.append(line)
            if clean_line.startswith(("if ", "loop ")):
                nest_level += 1
            elif clean_line in ["endif", "endloop"]:
                if nest_level > 0:
                    nest_level -= 1

    CLASSES[class_name] = {'methods': methods}

def load_module(module_path: str) -> Tuple[dict, dict, dict]:
    """
    Load another MSlash file in an isolated environment and return:
      (module_variables, module_functions, module_classes)
    """
    global FUNCTIONS, CLASSES
    saved_functions = FUNCTIONS
    saved_classes = CLASSES
    try:
        # Isolate module environment
        FUNCTIONS = {}
        CLASSES = {}
        with open(module_path, "r") as f:
            lines = f.readlines()
        _dbg(f"--- PREPROCESS MODULE {module_path} ---")
        main_script_lines = preprocess_script(lines)
        module_vars = {}
        _dbg(f"--- EXECUTE MODULE {module_path} ---")
        execute(main_script_lines, module_vars)
        module_functions = FUNCTIONS
        module_classes = CLASSES
        return module_vars, module_functions, module_classes
    finally:
        # Restore caller's global registries
        FUNCTIONS = saved_functions
        CLASSES = saved_classes

def _parse_cli():
    """
    Minimal CLI parser: accepts optional filename and flags:
      -d / --debug
    """
    filename = None
    global DEBUG
    for arg in sys.argv[1:]:
        if arg in ("-d", "--debug"):
            DEBUG = True
        elif not arg.startswith("-") and filename is None:
            filename = arg
        # ignore unknown flags for simplicity
    return filename

def main(target_file: str):
    """
    Main function to open the file, preprocess, and start script execution.
    """
    try:
        with open(target_file, "r") as file:
            lines = file.readlines()

        _dbg(f"--- PREPROCESS {target_file} ---")
        main_script_lines = preprocess_script(lines)

        if main_script_lines is not None:
            global_variables = {}
            _dbg("--- EXECUTE MAIN ---")
            execute(main_script_lines, global_variables)

    except FileNotFoundError:
        print(f"Error: File '{target_file}' not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

# --- Program Start ---
if __name__ == "__main__":
    cli_filename = _parse_cli()
    if cli_filename:
        main(cli_filename)
    else:
        target = input("Enter the name and file extension of the target file (e.g., demo.mslash): ")
        main(target)
