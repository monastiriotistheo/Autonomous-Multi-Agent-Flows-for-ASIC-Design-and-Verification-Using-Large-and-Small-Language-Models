#######################################################################
# Project: Agentic Verification Flow (DSPy + iVerilog)                #
# Author: Monastiriotis Theodoros                                     #
#                                                                     #
# Description:                                                        #
# An automated, multi-agent hardware verification framework that      #
# leverages Language Models via DSPy to generate, validate, and       #
# optimize Verilog-2001 testbenches against buggy implementation      #
# mutants.                                                            #
#                                                                     #
# Architecture:                                                       #
# 1. Coder Agent: Generates functional testbenches based on hardware  #
#    specifications, rules, and knowledge base prompts.               #
# 2. Validator Agent: Audits generated testbenches against a strict   #
#    checklist to ensure specification alignment and rule compliance. #
# 3. Distiller Agent: Extracts actionable, non-obvious engineering    #
#    lessons from verification transcripts into a persistent JSON     #
#    knowledge base, if enabled.                                      #
#                                                                     #
# Methodology:                                                        #
# Employs a self-correcting feedback loop alongside DSPy prompt       #
# optimization (MIPROv2) to iteratively isolate a single correct      #
# mutant through dynamic simulation-driven metric scoring.            #
#######################################################################

import os
import re
import sys
import json
import subprocess
import time
import dspy
from dspy.teleprompt import MIPROv2

# --- CONFIGURATION ---

# Change this to point to the subfolder of the design you want to verify
DESIGN_SUBFOLDER = "enc_bin2gray"

# Change the mode of the program:
# Optimize: Train using DSPy MIPROv2 optimizer to generate optimized knowledge
# Inference:Validate on unseen designs using DSPy's optimized knowledge
# Simple Run: Execute the baseline program, ignoring DSPy's optimized knowledge
# MODE = "Optimize"
# MODE = "Inference"
MODE = "Simple Run"

# How many lessons to keep per category in knowledge base.
# Oldest lessons beyond this limit are pruned when new ones arrive.
KB_MAX_LESSONS_PER_CATEGORY = 20

# If False, KnowledgeDistiller is enabled and KB is not injected in the Coder Agent. 
# If True, KnowledgeDistiller is disabled and KB is injected.
# AUTONOMOUS_MODE = False
AUTONOMOUS_MODE = True

# Change this to the model of your choice
SLM_MODEL = "ollama_chat/qwen2.5-coder:7b"
LLM_MODEL = "gemini/gemini-3-flash-preview"

# If True, dspy will reuse previously stored responses from the LM 
# for identical prompts to save time and API costs. Set to False if you want 
# to ensure every agent request generates a fresh response.
CACHE = False
# CACHE = True

# Setup local Ollama connection
SLM = dspy.LM(SLM_MODEL, api_base="http://localhost:11434", cache=CACHE)

# Setup remote Gemini connection
LLM = dspy.LM(LLM_MODEL, api_key='your_gemini_api_key', cache=CACHE)

# This configuration will be used for the Coder Agent
dspy.configure(lm=SLM)

# Get the absolute path of the folder where this script lives
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- AGENT RULES ---

VERILOG_CODER_RULES = """--- MANDATORY TB RULES (STRICT COMPLIANCE REQUIRED) ---

1. REPORTING (PER TEST CASE):
   - If any check fails, the testbench MUST print 'TEST FAILED' and call $finish immediately.
     Use 'if (condition) begin $display(\"TEST FAILED\"); $finish; end' for every check.
   - The string 'TEST PASSED' must ONLY be printed at the very end IF AND ONLY IF all checks passed.
     
2. CODING DISCIPLINE:
   - Use Verilog-2001 standards (reg/wire only).
   - Use only the provided signature for module instantiation.
   - Use only one 'initial' block. 
   - EXCEPTION: A second 'initial' block is ONLY allowed for clock generation, and ONLY IF the module signature contains a clock port.
   - CRITICAL: Do NOT use 'assert(variable==value)'. Use 'if' statements for verification logic.
   - All declarations (reg, wire, integer) MUST be at the top of the module, before any assignments or initial blocks.
   - All procedural delays involving math or parameters MUST be enclosed in parentheses like '#(DELAY_VAL * 2)'.

3. LOOPING PROTOCOL (IF LOOPS ARE REQUIRED):
   - If a loop is needed, use an 'integer i' declared at the top for the loop control.
   - NEVER use the input 'reg' as the 'for' loop variable.

4. DYNAMIC VERIFICATION:
   - NEVER hard-code expected output values for multi-bit data outputs (e.g., 'if (out == 8'b10101010)').
   - ALWAYS calculate the "Golden Value" dynamically within the testbench using the reference logic expression.
   - PROCEDURE: 
      -> Declare a temporary 'reg' of the same bit-width as the DUT output AT THE TOP of the module.
      -> Assign the result of the reference expression to this temporary 'reg'.
      -> Compare the DUT output against this temporary 'reg' using the '!==' operator.
   - EXCEPTION: Simple 1-bit control/valid signals may be checked directly with '==' or '!==' against a literal 1'b0 or 1'b1.
"""

VERILOG_VALIDATOR_RULES = f""" --- MANDATORY VALIDATION CHECKLIST --- 

A. SPECIFICATION ALIGNMENT: Are there any missing requirements or edge cases described in the specification?
B. MATHEMATICAL ACCURACY: Are the 'expected' values mathematically correct for the inputs?
C. MUTANT DIFFERENTIATION: Is the testbench strict enough to isolate ONLY one mutant (verilog implementaion) among buggy versions?
D. TB RULE COMPLIANCE: Is the testbench following these specific MANDATORY TB RULES:

{VERILOG_CODER_RULES}

NOTE ON COMPLIANCE: Do not flag variables not declared at the top as a violation unless it results in a documented compilation error in the evaluator feedback.
"""

# --- SIGNATURES ---

class VerilogCoder(dspy.Signature):
    """
    You are an expert Design Verification Engineer. Generate a testbench based on the provided specification 
    and simulation rules to isolate the ONLY ONE correct mutant (verilog implementation) among buggy versions.
    """
    specification    = dspy.InputField(desc="Hardware specification for the design.")
    mutant_signature = dspy.InputField(desc="Structural reference for port names and mandatory module signature.")
    simulation_rules = dspy.InputField(desc="MANDATORY simulation rules")
    knowledge_base   = dspy.InputField(desc="Accumulated lessons from previous runs. Follow these as additional mandatory rules.")
    previous_code    = dspy.InputField(desc="Testbench Verilog code from the last iteration (if any).")
    feedback         = dspy.InputField(desc="Evaluator feedback and code of mutants that passed the testbench.")
    testbench_code   = dspy.OutputField(desc="Complete Verilog-2001 testbench, no explanation, no markdown, no timescale.")

class VerilogValidator(dspy.Signature):
    """
    You are an expert Design Verification Engineer. 
    Audit the Generated Verilog-2001 Testbench against the specification and the Mandatory Validation Checklist.
    """
    specification    = dspy.InputField(desc="Hardware specification for the design.")
    mutant_signature = dspy.InputField(desc="Mandatory module signature.")
    testbench_code   = dspy.InputField(desc="Testbench code to be validated.")
    validation_rules = dspy.InputField(desc="Mandatory validation checklist and testbench generation rules.")
    feedback         = dspy.InputField(desc="Compile errors and a list of mutants (verilog implementations) that PASSED. "
                                            "If count > 1, the testbench is too weak. The 'correct' mutant ID is unknown.")
    audit_report     = dspy.OutputField(desc=("MUST start with exactly 'Match' or 'Mismatch' on the first line. "
                                              "CRITICAL: If the feedback indicates that more than one mutant passed, you MUST report "
                                              "a 'Mismatch' and specify a concrete test case or assertion fix to disqualify them. "
                                              "If 'Mismatch', provide a concise list of missing edge cases, rule violations, or differentiation issues. "
                                              "Format each item as: '<issue description> -> FIX: <exact corrective action>. "
                                              "If 'Match' which means only one mutant passed, leave the list empty."))

class KnowledgeDistiller(dspy.Signature):
    """
    You are a senior verification architect whose job is to distill portable, reusable lessons
    from a completed verification run. These lessons will be stored in a JSON knowledge base
    and fed to a smaller, less capable language model (SLM) as extra rules.

    CRITICAL REQUIREMENTS for every lesson you produce:
    - Write in plain, imperative English. No jargon the SLM might not understand.
    - Be SPECIFIC and ACTIONABLE. Bad: "Be careful with loops." Good: "Always declare 'integer i'
      at the top of the module and use it as the for-loop variable; never reuse an input reg."
    - Each lesson must stand alone, assume the SLM has no memory of the run that generated it.
    - Do NOT duplicate rules already present in the existing knowledge base (shown below).
    - Focus on NON-OBVIOUS lessons: things that caused failures, required multiple retries,
      or were caught by the Validator but missed by the SLM.
    - Categorise each lesson into exactly one of these categories:
        syntax_and_style | dynamic_verification | edge_cases |
        mutant_differentiation | reporting | clock_and_timing | general_strategy
      You MAY introduce a new category name if none of the above fits well.
    """
    design_name       = dspy.InputField(desc="Name of the hardware design that was verified.")
    specification     = dspy.InputField(desc="Hardware specification for the design.")
    run_transcript    = dspy.InputField(desc=("Full transcript of the verification run: all iterations, evaluator feedback, validator reports, and testbench code."))
    existing_kb       = dspy.InputField(desc="Current knowledge base content (to avoid duplicates).")
    final_status      = dspy.InputField(desc="'VERIFIED' or 'FAILED', and the number of iterations taken.")
    new_lessons_json  = dspy.OutputField(desc=("A JSON array of lesson objects. Each object MUST have exactly two keys: "
                                               "'category' (string) and 'lesson' (string). "
                                               "Output ONLY the raw JSON array. No markdown fences, no explanation, no preamble. "
                                               "If there are no new lessons to add, output an empty array: []"))

# --- KNOWLEDGE BASE ---

class KnowledgeBase:
    """
    Persistent JSON knowledge base that accumulates lessons across runs.

    Schema
    ------
    {
        "meta": {
            "version": int,
            "total_runs": int,
            "total_iterations": int,
            "last_updated": str (ISO timestamp)
        },
        "lessons": {
            "<category>": [
                {
                    "id": str,
                    "lesson": str,          # plain-English rule for the SLM
                    "source_design": str,   # which design triggered this
                    "iteration": int,       # which iteration it came from
                    "run": int,             # global run index
                    "added": str            # ISO timestamp
                },
                ...
            ]
        }
    }

    Standard categories (the LLM may add new ones freely):
        - "syntax_and_style"
        - "dynamic_verification"
        - "edge_cases"
        - "mutant_differentiation"
        - "reporting"
        - "clock_and_timing"
        - "general_strategy"
    """

    DEFAULT_SCHEMA = {
        "meta": {
            "version": 1,
            "total_runs": 0,
            "total_iterations": 0,
            "last_updated": ""
        },
        "lessons": {}
    }

    def __init__(self, path: str):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        """Load the JSON file. Falls back to default if the file is missing or corrupted."""
        
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"\n[KB] Loaded knowledge base from {self.path}")
                return data
            
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[KB] Warning: Could not parse existing KB ({e}). Starting fresh.")
        
        # Return a copy of the default schema as fallback
        return json.loads(json.dumps(self.DEFAULT_SCHEMA))

    def save(self):
        """Saves the current state of the Knowledge Base to disk."""

        self.data["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self.path, "w", encoding="utf-8") as f:
            # indent = 2 to add 2 spaces in every nested line
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        
        print(f"[KB] Saved knowledge base to {self.path}")

    def increment_run(self) -> int:
        """Increments and returns the total run counter in the metadata."""

        self.data["meta"]["total_runs"] += 1
        return self.data["meta"]["total_runs"]

    def increment_iterations(self, n: int = 1):
        """Tracks the total number of AI 'attempts' made across all time."""
        
        self.data["meta"]["total_iterations"] += n

    def add_lessons(self, new_lessons: list[dict], design: str, run: int) -> int:
        """
        Merges new lessons into the KB, prevents duplicates, and handles pruning.
        Returns the number of unique lessons actually added.
        """

        lessons_map = self.data["lessons"]
        added_count = 0

        for entry in new_lessons:
            # Extract and clean category/lesson text
            category = entry.get("category", "general_strategy").strip()
            lesson_text = entry.get("lesson", "").strip()

            # Skip empty entries if the model returned malformed JSON
            if not lesson_text:
                continue

            # Avoid adding a lesson if it's too similar to an existing one by comparing the 
            # first 60 characters of the new lesson against everything in the current category.
            existing_texts = [l["lesson"] for l in lessons_map.get(category, [])]
            if any(lesson_text[:60] in t or t[:60] in lesson_text for t in existing_texts):
                continue

            # Create the category if it does not exist
            if category not in lessons_map:
                lessons_map[category] = []

            # Tag each lesson with its source to allow tracking of 
            # which hardware design taught the AI this specific rule.
            lesson_id = f"{design}_run{run}_{len(lessons_map[category]):03d}"
            lessons_map[category].append({
                "id": lesson_id,
                "lesson": lesson_text,
                "source_design": design,
                "run": run,
                "added": time.strftime("%Y-%m-%dT%H:%M:%S")
            })
            added_count += 1

            # Prune oldest entries if over limit. Keep only the 'N' most recent lessons per category.
            if len(lessons_map[category]) > KB_MAX_LESSONS_PER_CATEGORY:
                lessons_map[category] = lessons_map[category][-KB_MAX_LESSONS_PER_CATEGORY:]

        return added_count

    def as_lm_prompt(self) -> str:
        """
        Render the KB as a clean, structured block suitable for injection into the LM's prompt. 
        Returns a formatted string containing all lessons by category or an empty string if no lessons exist.
        """

        # If there is no data, return an empty string
        all_lessons = self.data["lessons"]
        if not any(all_lessons.values()):
            return ""

        lines = [
            "--- ACCUMULATED KNOWLEDGE BASE ---",
            "These rules were distilled from previous verification runs.",
            "Follow them strictly as they encode hard-won lessons.",
            ""
        ]

        # Sort categories alphabetically to keep the prompt consistent
        for category, entries in sorted(all_lessons.items()):
            if not entries:
                continue

            # Format category names for readability
            lines.append(f"[{category.upper().replace('_', ' ')}]")
            
            # Number the lessons 1, 2, 3... under each category
            for idx, entry in enumerate(entries, 1):
                lines.append(f"  {idx}. {entry['lesson']}")
            lines.append("")
        
        return "\n".join(lines)

    def summary(self) -> str:
        """Returns a status string of the current KB state."""

        meta = self.data["meta"]
        total = sum(len(v) for v in self.data["lessons"].values())
        return (f"Runs: {meta['total_runs']} | "
                f"Iterations: {meta['total_iterations']} | "
                f"Lessons: {total} across {len(self.data['lessons'])} categories")

# --- MODULE (The Execution Flow) ---

class VerilogVerificationFlow(dspy.Module):
    def __init__(self, mutant_dir: str, check_script: str, knowledge_base: KnowledgeBase):
        super().__init__()
        self.mutant_dir = mutant_dir
        self.check_script = check_script
        self.kb = knowledge_base

        self.coder = dspy.ChainOfThought(VerilogCoder)
        self.validator = dspy.Predict(VerilogValidator)
        self.distiller = dspy.Predict(KnowledgeDistiller)

        # Accumulates the full run transcript for the distiller
        self.transcript_lines: list[str] = []
    
    def _log(self, msg: str):
        """Append a line to the run transcript (in addition to stdout)."""

        print(msg)
        self.transcript_lines.append(msg)

    def get_file_content(self, filename: str) -> str:
        """Read local file content to be sent as text to the LM."""

        path = os.path.join(self.mutant_dir, filename)
        if os.path.exists(path):
            with open(path, "r") as f:
                return f.read()

        return ""

    def run_evaluator(self, tb_code: str, iteration: int) -> tuple[int, list[str], str, str]:
        """Helper to call the existing mutant_evaluator.py."""

        # Save as a unique iteration file for debugging
        iteration_filename = f"tb_iteration_{iteration}.v"
        iteration_path = os.path.join(self.mutant_dir, iteration_filename)
        
        clean_tb = self.extract_verilog(tb_code)
        
        with open(iteration_path, "w") as f:
            f.write(clean_tb)
        print(f"Saved testbench to: {iteration_filename}")

        # Save as the standard 'tb.v' for the evaluator to use
        tb_path = os.path.join(self.mutant_dir, "tb.v")
        with open(tb_path, "w") as f:
            f.write(clean_tb)
        
        # Run the evaluation script
        self._log(f"\n--- Running Verification (Attempt {iteration}) ---")
        try:
            result = subprocess.run(
                self.check_script,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.mutant_dir,
                timeout=30
            )
            feedback = result.stdout + result.stderr

        except subprocess.TimeoutExpired as e:
            feedback = (e.stdout or "") + "\nEvaluation timed out after 30 seconds.\n"

        print(feedback)

        # Remove ANSI escape sequences (colors) from the feedback string
        # to print feedback correctly inside inspect_history() and transcript.
        # This regex matches the standard \033[...m patterns
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        feedback = ansi_escape.sub('', feedback)
        self.transcript_lines.append(feedback)

        # Count how many mutants passed
        passed_mutants = re.findall(r"(mutant_\d+).*?PASSED", feedback)
        return len(passed_mutants), passed_mutants, feedback, clean_tb
    
    def extract_verilog(self, raw: str) -> str:
        """Extract and sanitize TB from raw LM output."""
        
        # Strip any opening fence line (e.g. ```verilog, ```)
        raw = re.sub(r"^```\w*\n", "", raw.strip())
        # Strip any closing fence line
        raw = re.sub(r"\n```$", "", raw.strip())

        # If no module found after stripping fences, try to find module...endmodule directly
        m = re.search(r"(module\s+\w+.*?endmodule)", raw, re.DOTALL)
        code = m.group(1).strip() if m else raw.strip()

        # Remove stray backticks before comments (` // comment → // comment)
        # Valid Verilog directives start with `define `include `timescale etc.
        code = re.sub(r"`(?!(define|include|timescale|ifdef|ifndef|endif|else|undef)\b)", "", code)

        # Remove existing timescale lines (if any)
        # This matches `timescale followed by anything until the end of that line
        code = re.sub(r"`timescale\s+.*?\n", "", code).strip()

        # Manually add timescale and return the clean code
        return "`timescale 1ns/1ps\n\n" + code

    def _save_transcript(self, design_name: str, run_index: int):
        """Save the full run transcript to a timestamped file for inspection."""

        filename = f"transcript_run{run_index}_{design_name}.txt"
        path = os.path.join(self.mutant_dir, filename)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.transcript_lines))

        print(f"[TRANSCRIPT] Saved to: {filename}")

    def distill_knowledge(self, specification: str, design_name: str, final_status: str, run_index: int):
        """
        Ask the KnowledgeDistiller agent to extract new lessons from
        a single run's transcript and merge them into the persistent KB.
        """

        self._log("\n[KB] Starting knowledge distillation...")

        # Load kb and transcript for the distiller
        existing_kb_text = self.kb.as_lm_prompt() or "(empty, this is the first run)"
        run_transcript = "\n".join(self.transcript_lines)

        try:
            with dspy.context(lm=LLM):
                distill_res = self.distiller(
                    design_name=design_name,
                    specification=specification,
                    run_transcript=run_transcript,
                    existing_kb=existing_kb_text,
                    final_status=final_status,
                )

            raw_json = distill_res.new_lessons_json.strip()

            # Strip markdown fences if the model added them anyway
            raw_json = re.sub(r"^```\w*\n?", "", raw_json)
            raw_json = re.sub(r"\n?```$", "", raw_json).strip()

            new_lessons = json.loads(raw_json)

            # Ensure the agent actually returned a list/array
            if not isinstance(new_lessons, list):
                raise ValueError("Expected a JSON array from distiller.")

            added = self.kb.add_lessons(new_lessons, design=design_name, run=run_index)
            self._log(f"[KB] Distillation complete. {added} new lesson(s) added.")
            self._log(f"[KB] Current KB stats: {self.kb.summary()}")

        except (json.JSONDecodeError, ValueError) as e:
            self._log(f"[KB] Warning: Could not parse distiller output ({e}). Skipping update.")
            self._log(f"[KB] Raw distiller output was:\n{distill_res.new_lessons_json if 'distill_res' in dir() else 'N/A'}")

    def forward(self, specification: str, mutant_signature: str, subfolder: str = None) -> dspy.Prediction:
        feedback = "Initial attempt. Write a rigorous testbench to pass ONLY the correct mutant."
        tb_code = "None (First Attempt)"

        # Provide the correct subfolder when running optimize mode
        if subfolder:
            self.mutant_dir = os.path.join(ROOT_DIR, subfolder)

        # Pull current KB content to inject into LM prompt
        kb_prompt = self.kb.as_lm_prompt() if AUTONOMOUS_MODE else ""

        if AUTONOMOUS_MODE and kb_prompt:
            self._log(f"\n[KB] Injecting {len(kb_prompt.splitlines())} lines of KB knowledge into Coder Agent.")
        elif AUTONOMOUS_MODE and not kb_prompt:
            self._log("\n[KB] AUTONOMOUS mode but knowledge base is empty. Coder Agent will rely on rules and spec only.")
        else:
            self._log("\n[KB] TRAINING mode: KB injection disabled. Distiller will learn from this run.")

        max_iterations = 10
        for i in range(1, max_iterations + 1):
            self._log(f"\nIteration {i}/{max_iterations}: Requesting a testbench...")

            # Generate/Refine TB
            coder_res = self.coder(
                specification=specification, 
                mutant_signature=mutant_signature, 
                simulation_rules=VERILOG_CODER_RULES, 
                knowledge_base=kb_prompt or "(No accumulated lessons yet.)",
                previous_code=tb_code, 
                feedback=feedback
            )

            # Print lm input prompt, reasoning and output testbench
            current_lm = dspy.settings.lm
            current_lm.inspect_history(n=1)

            self.transcript_lines.append(
                f"\n--- CODER REASONING (Iteration {i}) ---\n"
                f"{coder_res.reasoning}\n"
                f"--- END REASONING ---\n"
            )

            # Run the evaluation script
            pass_count, _, eval_feedback, tb_code = self.run_evaluator(coder_res.testbench_code, i)
            self._log(f"Iteration {i}: {pass_count} mutants passed.")

            self.transcript_lines.append(
                f"\n--- TESTBENCH CODE (Iteration {i}) ---\n"
                f"{tb_code}\n"
                f"--- END TESTBENCH (Iteration {i}) ---\n"
            )

            if pass_count != 1 and i == max_iterations:
                self._log("Reached max iterations. Could not isolate the correct mutant.")
                break

            # Validate against spec and sim rules
            self._log("Requesting Validator Agent to validate against specification and simulation rules...")
            with dspy.context(lm=LLM):
                val_res = self.validator(
                    specification=specification, 
                    mutant_signature=mutant_signature, 
                    testbench_code=tb_code, 
                    validation_rules=VERILOG_VALIDATOR_RULES, 
                    feedback=f"{pass_count} mutants passed.\n{eval_feedback}"
                )

            self._log("\n--- AGENT VALIDATION REPORT ---")
            self._log(val_res.audit_report)
            self._log("="*30 + "\n")
            
            # Capture the validation result and check for Mismatch
            validation_report = val_res.audit_report
            validation_mismatch = "MISMATCH" in validation_report.upper().splitlines()[0]

            if "COMPILATION ERROR" in eval_feedback.upper():
                self._log("Syntax error detected. Requesting fix...")
                feedback = (
                    f"The previous testbench had syntax errors. Please fix them.\n"
                    f"--- EVALUATOR FEEDBACK & SYNTAX ERRORS ---\n{eval_feedback}"
                )
            
            elif pass_count == 1:
                if validation_mismatch:
                    self._log("Validator Agent found issues. Passing feedback back to Coder Agent...")
                    feedback = ("Isolated 1 mutant, but Validator found the following issues. Please fix them.")
                    # Validation report will be added in the end of loop
                else:
                    if not AUTONOMOUS_MODE:
                        # Distill knowledge from this run before returning
                        final_status = f"VERIFIED in {i} iterations"
                        run_index = self.kb.increment_run()
                        self.kb.increment_iterations(i)
                        self.distill_knowledge(specification, DESIGN_SUBFOLDER, final_status, run_index)
                        self.kb.save()
                        self._save_transcript(DESIGN_SUBFOLDER, run_index)
                    return dspy.Prediction(testbench_code=coder_res.testbench_code, iterations=i, status="VERIFIED")

            elif pass_count > 1 and pass_count != 31:
                feedback = (
                    f"Your testbench is weak, {pass_count} mutants passed. "
                    f"You must differentiate the correct one from these surviving mutants.\n"
                    f"--- EVALUATOR FEEDBACK ---\n{eval_feedback}"
                )
            
            elif pass_count == 31:
                feedback = (
                    f"Your testbench is too weak or you are violating the MANDATORY simulation rules provided.\n"
                    f"All 31 mutants passed! You must differentiate the correct one. Provide an improved testbench."
                )
            
            else:
                feedback = (
                    f"Your testbench is too strict or you are violating the MANDATORY simulation rules provided.\n"
                    f"0 mutants passed. Provide an improved testbench."
                )

            # Append Validator info if there's a mismatch
            if validation_mismatch:
                feedback += f"\n--- VALIDATOR ISSUES TO FIX ---\n{validation_report}"

        # Run ended (FAILED).
        if not AUTONOMOUS_MODE:
            # Distill knowledge from this run before returning
            final_status = f"FAILED after {max_iterations} iterations"
            run_index = self.kb.increment_run()
            self.kb.increment_iterations(max_iterations)
            self.distill_knowledge(specification, DESIGN_SUBFOLDER, final_status, run_index)
            self.kb.save()
            self._save_transcript(DESIGN_SUBFOLDER, run_index)
        return dspy.Prediction(testbench_code=None, iterations=max_iterations, status="FAILED")

# --- OPTIMIZATION ---

def build_trainset(flow, designs: list[dict]) -> list[dspy.Example]:
    """
    Build a DSPy trainset from a list of design dicts.
    Each dict must have:
        - 'subfolder': str  -> the design folder name
        - 'spec_file': str  -> filename of the spec   (default: 'specification.md')
        - 'sig_file':  str  -> filename of mutant_0.v (default: 'mutant_0.v')

    A known-good testbench ('tb_golden.v') is OPTIONAL.
    If present it is attached so MIPROv2 can use it as a labeled demo.
    """

    examples = []
    for design in designs:
        subfolder  = design["subfolder"]
        spec_file  = design.get("spec_file", "specification.md")
        sig_file   = design.get("sig_file",  "mutant_0.v")
        gold_file  = design.get("gold_tb",   "tb_golden.v")   # optional

        # Temporarily point flow at this design's folder
        original_dir      = flow.mutant_dir
        flow.mutant_dir   = os.path.join(ROOT_DIR, subfolder)

        spec      = flow.get_file_content(spec_file)
        full_sig  = flow.get_file_content(sig_file)
        gold_tb   = flow.get_file_content(gold_file)   # empty string if missing

        flow.mutant_dir = original_dir  # restore

        if not spec or not full_sig:
            print(f"Skipping '{subfolder}': missing spec or signature.")
            continue

        lines = full_sig.strip().splitlines()
        mutant_signature = next((l for l in lines if "module" in l), lines[0])

        ex = dspy.Example(
            specification    = spec,
            mutant_signature = mutant_signature,
            subfolder        = subfolder,
        )

        # If there is a gold testbench, attach it
        if gold_tb:
            ex["testbench_code"] = gold_tb

        # Define the inputs that the forward() function actually accepts
        ex = ex.with_inputs("specification", "mutant_signature", "subfolder")

        examples.append(ex)
        print(f"Added '{subfolder}' to trainset (gold_tb={'yes' if gold_tb else 'no'})")

    return examples

def make_metric(flow: VerilogVerificationFlow) -> callable:
    """
    Returns a DSPy-compatible metric function.

    Scoring:
        1.0  -> exactly 1 mutant passed  (perfect isolation)
        0.1  -> 0 mutants passed         (compiled but too strict)
        0.0  -> all 31 passed            (trivially weak / compile error)
        grad -> partial credit           (fewer survivors = better)
    """

    # Track how many simulation trials have been run.
    # Use a dictionary so the value persists across multiple calls to verilog_metric.
    iteration_counter = {"n": 0}

    def verilog_metric(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> float:
        print("[DEBUG] verilog_metric() call")

        # Extract the generated Verilog code from the AI's prediction
        tb_code = getattr(prediction, "testbench_code", None)
        if not tb_code:
            return 0.0

        # Set the correct design folder before calling run_evaluator
        design = example.get("subfolder", None)
        if design:
            flow.mutant_dir = os.path.join(ROOT_DIR, design)

        # Increment the counter to ensure unique log filenames for this trial
        iteration_counter["n"] += 1
        pass_count, _, _, _ = flow.run_evaluator(tb_code, iteration_counter["n"])

        if pass_count == 1:
            score = 1.0
        elif pass_count == 0:
            score = 0.1
        elif pass_count == 31:
            score = 0.0
        else:
            # Calculate a score based on how many mutants were "killed"
            score = max(0.0, 1.0 - (pass_count / 31))

        print(f"[METRIC] pass_count = {pass_count}, score = {score:.3f}")
        return score

    return verilog_metric

def run_mipro_optimization(flow: VerilogVerificationFlow, trainset: list[dspy.Example]) -> dspy.Module:
    """
    Runs MIPROv2 prompt optimization using as a teacher the LLM.
    Only the SLM ChainOfThought module is optimized.
    The optimized program is saved to 'optimized_coder.json'.
    """

    metric = make_metric(flow)

    # Set up the optimization coach
    teleprompter = MIPROv2(
        metric                 = metric,       # The judge used to grade the testbenches
        prompt_model           = LLM,          # The Teacher who writes new instructions
        task_model             = SLM,          # The Student who generates the code
        teacher_settings       = dict(lm=LLM), # Use LLM for the "heavy thinking" steps
        auto                   = None,         # Enable manual setting of num_candidates and num_trials
        num_candidates         = 3,            # How many different prompt versions LLM will generate. One of these is picked in each trial
        max_bootstrapped_demos = 2,            # Number of successful traces to include in the prompt
        max_labeled_demos      = 1,            # Number of raw examples from trainset
        num_threads            = 1,            # Run one model instance at a time
        max_errors             = 5,            # Stop if 5 consecutive trials throw exceptions
        verbose                = True,         # Print everything to the screen
    )

    print("\n" + "="*60)
    print("  Starting MIPROv2 Prompt Optimization...")
    print(f"Teacher : {LLM_MODEL} (proposes instructions + few-shot demos)")
    print(f"Student : {SLM_MODEL} (executes — NO weight updates)")
    print("="*60 + "\n")

    # Start the training process
    optimized_flow = teleprompter.compile(
        flow,
        trainset                   = trainset, 
        num_trials                 = 3,        # Number of rounds (trials) to search for the best prompt-demo combination
        minibatch                  = True,     # Check minibatch_size designs in each trial
        minibatch_size             = 1,        # Check only 1 design from valset in each trial
    )

    # Save the optimized prompt program
    save_path = os.path.join(ROOT_DIR, "optimized_coder.json")
    optimized_flow.save(save_path)
    print(f"\nOptimized prompt program saved to: {save_path}")

    return optimized_flow

def load_optimized_flow(flow: VerilogVerificationFlow) -> VerilogVerificationFlow:
    """Load a previously optimized prompt program."""
    
    save_path = os.path.join(ROOT_DIR, "optimized_coder.json")

    if not os.path.exists(save_path):
        print("No optimized program found. Run optimization first.")
        return flow

    flow.load(save_path)
    print(f"Loaded optimized prompt program from: {save_path}")
    return flow

# --- UTILITIES ---

def setup_logging():
    """Redirect stdout and stderr to terminal (with color) and log file (clean text)."""
    
    class Logger(object):
        def __init__(self, terminal, log_file):
            self.terminal = terminal
            self.log = log_file
            # Regex to match ANSI escape sequences (colors)
            self.ansi_escape = re.compile(r'\x1b\[[0-9;]*[mK]')

        def write(self, message):
            # Write the original message (with colors) to the terminal
            self.terminal.write(message)
            
            # Strip colors and write to the log file
            clean_message = self.ansi_escape.sub('', message)
            self.log.write(clean_message)
            self.log.flush()

        def flush(self):
            self.terminal.flush()
            self.log.flush()

    # Open the log file
    f = open(f"{DESIGN_SUBFOLDER}/{DESIGN_SUBFOLDER}.log", "w", encoding="utf-8")
    
    # Replace stdout and stderr
    sys.stdout = Logger(sys.stdout, f)
    sys.stderr = sys.stdout

# --- MAIN EXECUTION ---

def main():
    setup_logging()

    if AUTONOMOUS_MODE:
        mode_label = "AUTONOMOUS (KB Injection, TB Validator only, No Distiller)" 
    else: 
        mode_label = "SUPERVISED (No KB Injection, TB Validator + KB Distiller)"

    print(f"Coder Agent Model                : {SLM_MODEL}")
    print(f"Validator Agent Model            : {LLM_MODEL}")
    print(f"Operation Mode                   : {mode_label}")
    print(f"DSPy Mode Selected               : {MODE}")
    print(f"Starting Agentic Flow for Design : {DESIGN_SUBFOLDER}")
    start_time = time.time()

    # The subfolder with the mutants
    MUTANT_DIR = os.path.join(ROOT_DIR, DESIGN_SUBFOLDER)

    # Path to the persistent knowledge base JSON file.
    KB_PATH = os.path.join(ROOT_DIR, "slm_knowledge_base.json")

    # Load (or create) the persistent knowledge base
    kb = KnowledgeBase(KB_PATH)
    print(f"\n[KB] Status: {kb.summary()}")

    # Point to the evaluator in the ROOT_DIR
    eval_cmd = f"python3 {os.path.join(ROOT_DIR, 'mutant_evaluator.py')}"
    flow = VerilogVerificationFlow(MUTANT_DIR, eval_cmd, kb)

    if MODE == "Optimize":
        # Define the training designs
        designs = [
            {"subfolder": "enc_bin2gray"},
            {"subfolder": "ecc_sed_encoder"},
            {"subfolder": "shift_right"},
        ]
        trainset = build_trainset(flow, designs)

        if not trainset:
            print("Trainset is empty. Check your design folders.")
            return

        flow = run_mipro_optimization(flow, trainset)
        print(f"MIPROv2 optimization time: {time.time() - start_time:.2f} seconds")
        return

    elif MODE == "Inference":
        # Load the saved optimized prompts and run normally
        flow = load_optimized_flow(flow)

    # Restore target design dir (optimize mode changes it)
    flow.mutant_dir = MUTANT_DIR
    
    print("\nReading specification and module signature...")
    spec = flow.get_file_content("specification.md")

    # Get mutant signature from mutant_0.v
    full_m0 = flow.get_file_content("mutant_0.v")
    if full_m0:
        # Split by lines and take the first one that starts with 'module'
        lines = full_m0.strip().splitlines()
        # Find the first line containing 'module' to avoid empty lines or comments
        mutant_signature = next((line for line in lines if "module" in line), lines[0])
        print(f"Captured Signature: {mutant_signature}")
    else:
        mutant_signature = ""
    
    if not spec or not mutant_signature:
        print(f"Error: Could not read specification.md or module signature in {MUTANT_DIR}")
        return
   
    # Start the flow
    result = flow(specification=spec, mutant_signature=mutant_signature)

    # Success
    if result.status == "VERIFIED":
        print("\nFinal Result: VERIFIED. The testbench is correct and complete.")
        print(f"Passed in {result.iterations} iterations.")

    print(f"Total Time: {time.time() - start_time:.2f} seconds")

if __name__ == "__main__":
    main()
