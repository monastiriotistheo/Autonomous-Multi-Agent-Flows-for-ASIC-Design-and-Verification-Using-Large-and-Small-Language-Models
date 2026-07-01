########################################################################
# Project: Agentic ASIC Design Flow (DSPy + iVerilog + OpenROAD)       #
# Author: Monastiriotis Theodoros                                      #
#                                                                      #
# Description:                                                         #
# An automated, iterative hardware development framework that uses     #
# Language Models via DSPy to transform high-level YAML specs into     #
# verified, synthesizable RTL and physical ODB layouts.                #
#                                                                      #
# Architecture:                                                        #
# 1. TB Flow: Generates and self-validates Verilog-2001 testbenches.   #
# 2. SDC Flow: Creates timing constraints based on spec targets.       #
# 3. RTL Flow: Iterative generation with functional and gate-level     #
#    simulation feedback loop (Icarus Verilog + Yosys).                #
# 4. Physical Flow: Automates the OpenROAD Flow Scripts (ORFS) to      #
#    produce final ODB artifacts for a specified platform.             #
#                                                                      #
# Methodology:                                                         #
# Employs Chain-of-Thought (CoT) reasoning and rigorous hardware       #
# rule enforcement (Zero-Latch, FSM styles etc.) to ensure that        #
# AI-generated code is physically realizable and timing-closed. It     #
# also provides DSPy optimization and knowledge extraction mechanisms. #
########################################################################

import dspy
import sys
import yaml
import json
import re
import time
import subprocess
import shutil
import tempfile
import threading
from pathlib import Path
from dspy.teleprompt import MIPROv2

# --- LM CONFIGURATION ---

# Change this to the model of your choice
SLM_MODEL = "ollama_chat/qwen2.5-coder:7b"
LLM_MODEL = "gemini/gemini-3-flash-preview"

# If True, dspy will reuse previously stored responses from the LLM 
# for identical prompts to save time and API costs. Set to False if you want 
# to ensure every agent request generates a fresh response.
CACHE = False
# CACHE = True

# Setup local Ollama connection and remote Gemini connection
SLM = dspy.LM(SLM_MODEL, api_base="http://localhost:11434", cache=CACHE)
LLM = dspy.LM(LLM_MODEL, api_key='your_gemini_api_key', cache=CACHE)

# Set default dspy configuration to the SLM
# This model is used for the RTL Generator
# when "Optimize" mode is enabled.
dspy.configure(lm=SLM)

# Change this to the model of your choice for each Agent
TB_GENERATOR_LM            = LLM
TB_VALIDATOR_LM            = LLM
SDC_GENERATOR_LM           = SLM
RTL_GENERATOR_LM           = SLM
RTL_VALIDATOR_LM           = LLM
RTL_KNOWLEDGE_DISTILLER_LM = LLM
CONFIG_MK_GENERATOR_LM     = SLM

# --- FLOW CONFIGURATION ---

# Set DESIGN and ORFS_PLATFORM to your choice
DESIGN        = "p1.yaml"              # The specific spec
ORFS_PLATFORM = "sky130hd"             # Target platform for ORFS
PDK_RTL_PATH  = Path("./PDK_files/")   # Verilog simulation models for the targeted platform

# If False, RTL Validator is enabled; if True is disabled
SKIP_RTL_VALIDATOR = False
# SKIP_RTL_VALIDATOR = True

# Change the mode of the RTL generation flow:
# Optimize:   Train using DSPy MIPROv2 optimizer to generate optimized knowledge
# Inference:  Validate on unseen designs using DSPy's optimized knowledge
# Simple Run: Execute the baseline program, ignoring DSPy's optimized knowledge
# RTL_GEN_DSPY_MODE = "Optimize"
# RTL_GEN_DSPY_MODE = "Inference"
RTL_GEN_DSPY_MODE = "Simple Run"

# Training designs for MIPROv2 optimization
# Change this to the traineset of your choice
RTL_TRAIN_DESIGNS = ["p1.yaml", "p5.yaml", "p7.yaml"]

# Path of the optimized RTL generation flow after optimization
OPTIMIZED_RTL_FLOW_PATH = Path("../results/optimized_rtl_generator.json")

# Set path to the OpenROAD-flow-scripts directory.
# If None, auto-detected from 'openroad' binary in PATH.
ORFS_DIR = None 
# ORFS_DIR = Path("/home/gmrd01/iclad_hackathon/tools/OpenROAD-flow-scripts/").expanduser()

# Flow overrides: Set to None to run the full generation flow for that stage.
# Set to a specific Path to skip generation and use the provided file directly.
TB_PATH     = None
SDC_PATH    = None
RTL_PATH    = None
CONFIG_PATH = None

# Specifying one of the following parameters uses the provided file and skips the corresponding flow
# TB_PATH     = Path(f"../results/visible/p1/tb_gen_flow/seq_detector_0011_tb.v")
# SDC_PATH    = Path(f"../results/visible/p1/sdc_gen_flow/seq_detector_0011.sdc")
# RTL_PATH    = Path(f"../results/visible/p1/rtl_gen_flow/seq_detector_0011.v")
# CONFIG_PATH = Path(f"../results/visible/p1/physical_flow/seq_detector_0011_config.mk")

# Set maximum iterations for each flow
MAX_TB_GEN_ITERS   = 10   # Max iterations of TB generation flow
MAX_RTL_GEN_ITERS  = 10   # Max iterations of RTL generation flow
MAX_SDC_GEN_ITERS  = 10   # Max iterations of SDC generation flow
MAX_PHYSICAL_ITERS = 10   # Max iterations of physical flow

# Set starting iteration for each flow
TB_GEN_ITER   = 1   # Master counter of TB generation flow
RTL_GEN_ITER  = 1   # Master counter of RTL generation flow
SDC_GEN_ITER  = 1   # Master counter of SDC generation flow
PHYSICAL_ITER = 1   # Master counter of physical flow

EVALUATION_DIR = Path("../evaluation/")                             # The folder where evaluation scripts live
PROBLEMS_DIR   = Path("../problems/visible/")                       # The folder where the specs (Problems) live
RESULTS_DIR    = Path("../results/visible") / Path(DESIGN).stem     # Intermediate results saving directory
SOLUTIONS_DIR  = Path("../solutions/visible") / Path(DESIGN).stem   # Solution saving directory

TB_GEN_DIR     = RESULTS_DIR / "tb_gen_flow"     # Results for tb generation flow
RTL_GEN_DIR    = RESULTS_DIR / "rtl_gen_flow"    # Results for rtl generation flow
SDC_GEN_DIR    = RESULTS_DIR / "sdc_gen_flow"    # Results for sdc generation flow
PHYSICAL_DIR   = RESULTS_DIR / "physical_flow"   # Results for physical flow

CONFIG_TEMPLATE   = Path("./templates/config.mk")           # config.mk template for the agent to fill in
SDC_COMB_TEMPLATE = Path("./templates/constraint_comb.sdc") # Combinational SDC template for the agent to fill in
SDC_SEQ_TEMPLATE  = Path("./templates/constraint_seq.sdc")  # Sequential SDC template for the agent to fill in

# --- KNOWLEDGE BASE CONFIGURATION ---

# How many lessons to keep per category in knowledge base.
# Oldest lessons beyond this limit are pruned when new ones arrive.
KB_MAX_LESSONS_PER_CATEGORY = 20

# If False, KnowledgeDistiller is enabled and KB is not injected in the RTL Generator. 
# If True, KnowledgeDistiller is disabled and KB is injected.
# AUTONOMOUS_MODE = False
AUTONOMOUS_MODE = True

# If False, the whole knowledge base is provided as LM prompt.
# If True, only the specific design lessons are provided.
KB_FILTER_BY_DESIGN = False
# KB_FILTER_BY_DESIGN = True

# Path of the knowledge base for RTL generation flow
KB_PATH = Path("../solutions/slm_knowledge_base.json")

# --- ANSI colors ---

Y  = "\033[33m"  # yellow -> Warnings
C  = "\033[36m"  # cyan   -> Flows headers
G  = "\033[32m"  # green  -> Success
RD = "\033[31m"  # red    -> Failure
B  = "\033[1m"   # bold
R  = "\033[0m"   # reset

# --- AGENT RULES ---

TB_GENERATOR_RULES = """--- MANDATORY TB RULES (STRICT COMPLIANCE REQUIRED) ---

1. CLOCK & RESET:
   - Generate a clock matching the 'clock_period' from the YAML.
   - Assert reset at time 0.
   - Initialize all other inputs to prevent 'X' propagation.

2. DRIVE & SAMPLE PROTOCOL:
   - Drive all DUT inputs on the 'negedge clk'.
   - Sample and verify DUT outputs on the 'posedge clk'.

3. REPORTING (PER TEST CASE):
   - Every comparison must print: 
     "TEST: [PASS/FAIL] | Inputs: [Name=Value...] | Expected: [Value] | Output: [Value]"

4. CODING DISCIPLINE & TERMINATION:
   - Use Verilog-2001 standards (reg/wire only).
   - CRITICAL: Do NOT use 'assert(variable==value)'. Use 'if' statements for verification logic.
   - Every task or block waiting for a response signal (e.g., 'valid') MUST set a maximum wait time.
   - Include '$finish' EXACTLY ONCE at the end of the testbench.
"""

TB_VALIDATOR_RULES = f""" --- MANDATORY VALIDATION CHECKLIST --- 

A. SPECIFICATION ALIGNMENT: Are there any missing requirements or edge cases described in the specification?
B. MATHEMATICAL ACCURACY: Are the 'expected' values mathematically correct for the inputs?
C. TB RULE COMPLIANCE: Is the testbench following these specific MANDATORY TB RULES:

{TB_GENERATOR_RULES}
"""

SDC_GENERATOR_RULES = """--- MANDATORY SDC RULES (STRICT COMPLIANCE REQUIRED) ---

1. TEMPLATE SELECTION:
   - Analyze the YAML spec and module signature for clock signals (e.g., clk, clock, sys_clk).
   - If a clock is present: Use the SEQUENTIAL template (Option A).
   - If NO clock is present: Use the COMBINATIONAL template (Option B).
   - DO NOT mix structures. Choose one and stick to its specific commands.

2. FILL ALL PLACEHOLDERS: 
   - You must replace every instance of text enclosed in angle brackets (<PLACEHOLDER>) with actual values. 
   - DO NOT leave any "< >" symbols in the final output.
   - DO NOT invent new variable names; only fill the ones provided in the chosen template.
   - You must NOT modify any other character, variable name, or command in the template.

3. PLACEHOLDER MAPPING:
   - <CLOCK_NAME>   -> The clock name defined in YAML.
   - <CLOCK_PORT>   -> The actual port name from the module signature (CASE-SENSITIVE).
   - <PERIOD_NS>    -> The numerical clock_period value from YAML (e.g., '2ns' becomes 2.0)..

4. COMBINATIONAL DESIGNS (no clock in signature):
   - Use the Combinational Template and ONLY fill the <PERIOD_NS> placeholder 
   - DO NOT include create_clock or set_input_delay/set_output_delay commands!!!
"""

RTL_GENERATOR_RULES = """--- MANDATORY RTL RULES (STRICT COMPLIANCE REQUIRED) ---

1. NO MULTI-DRIVER CONFLICTS:
   - Every signal (reg/wire) must be assigned in EXACTLY ONE 'always' block or ONE 'assign' statement.
   - NEVER assign a signal in the sequential block if it is also assigned in a combinatorial block.

2. CODING DISCIPLINE:
   - SYNTAX: Use Verilog-2001 standards. NO 'logic' or 'int' types. Use 'reg' and 'wire' only.
   - ASSIGNMENTS: Strictly use '<=' for sequential 'always' and '=' for combinatorial 'always' blocks.
   - BLOCKS: ALWAYS use 'begin...end' for every 'if', 'else', and 'always' block, regardless of command count.
   - SCOPE: Declare all 'reg' and 'wire' signals at the top of the module.
   - NON-SYNTHESIZABLE TYPES: NEVER use 'real' types. They will be rejected by the synthesis tool.
   - LATCH PREVENTION:
     * Start every combinatorial always block by assigning a default value to ALL its outputs.
     * Every 'if' must have an 'else'.
     * Every 'case' must have a 'default'.

3. TIMING & PIPELINE ARCHITECTURE (PERFORMANCE COMPLIANCE):
   - TIMING CLOSURE :
     * The RTL MUST be created to meet the clock_period defined in the specification.
     * NEVER chain multiple heavy operations (Multipliers, Dividers, Barrel Shifters) in a single combinatorial path.
   - PIPELINE ENFORCEMENT:
     * If the spec mentions "Pipelined", the design MUST use intermediate registers to break combinatorial paths.
     * Match the number of pipeline stages to the spec exactly. 
     * Balance the arithmetic workload across stages to ensure no single stage exceeds the clock period.
   - NON-LINEAR FUNCTION OPTIMIZATION:
     * For non-linear functions (e.g., x^3, 1/x, e^x, or complex coefficients) where the input width is less than 10 bits, MUST use a Look-Up Table. 
     * Always pre-calculate constant scaling (like Taylor coefficients) within the LUT initialization to eliminate post-multiplication logic.

4. STATE DEFINITIONS - IF REQUIRED:
   - Use 'localparam' to define states with UNIQUE binary values.
   - SIZING CONSISTENCY: Every state constant MUST be sized exactly to M bits (e.g., M'bBINARY).
   - REGISTER MATCHING: The state register 'reg [M-1:0] state' must have a width (M) that matches the localparam size exactly.
   - SUFFICIENCY: M must be large enough to represent all states (M >= ceil(log2(number_of_states))). 
     * Example: For 5 states, use M=3 (reg [2:0] state) and constants like 3'b000.

5. FSM DESIGN - IF REQUIRED (USE MOORE STYLE):
   - TWO-BLOCK ARCHITECTURE:
     * BLOCK A (Sequential): Use 'always @(posedge clk)' ONLY for updating the state register.
       Initialize the state to the 'IDLE' parameter inside the synchronous 'if (reset)' branch.
       DO NOT assign functional outputs in this block.
     * BLOCK B (Combinatorial): Use 'always @(*)' for next-state logic AND all functional outputs.
       TOP-LEVEL DEFAULTS: You MUST start this block by defining 'next_state = state;' and all outputs to their inactive/default values.
   - OUTPUT ASSERTION (MOORE STYLE):
     * Outputs must depend ONLY on the 'state' register.
     * Do NOT include input signals in the output assignment logic.
     * The output should be 1 while the 'state' equals the success state, NOT during the transition to it.
   - PROGRESSION & LOOPBACK: 
     * Every 'case' branch MUST define a path to 'next_state'.
     * After a Success/Detect state, the FSM MUST transition to the next logically valid state (e.g., IDLE or a PARTIAL-MATCH STATE).
     * A state MUST NOT transition to itself.
     
6. FORMATTING:
   - Ensure the module name and port list match the provided 'module_signature' EXACTLY. 
   - Use '//' for comments. NEVER use backticks (`) before a comment.
"""

RTL_VALIDATOR_RULES = f"""--- MANDATORY VALIDATION CHECKLIST ---

A. SPECIFICATION ALIGNMENT: Does the RTL correctly implement ALL functional requirements in the YAML spec 
   (e.g. pipeline stages, FSM states, LUT usage, port widths, arithmetic operations)?

B. SYNTHESIZABILITY: Does the RTL strictly follow these MANDATORY RTL RULES:

{RTL_GENERATOR_RULES}

C. TIMING & STRUCTURE: Are there any combinatorial paths that would violate the clock period? 
   Are pipeline stages balanced and correctly registered?

D. SIMULATION FAILURE ROOT CAUSE: Given the simulation/synthesis feedback, does the RTL have 
   a clear structural or logical bug that explains the failure?
"""

CONFIG_GENERATOR_RULES = """--- MANDATORY CONFIG RULES (STRICT COMPLIANCE REQUIRED) ---

1. FILL ALL PLACEHOLDERS: 
   - You must replace every instance of text enclosed in angle brackets (e.g., <CORE_UTILIZATION>) with actual values. 
   - DO NOT leave any "< >" symbols in the final output.
   - DO NOT invent new variable names; only fill the ones provided in the template.
   - You must NOT modify any other character, variable name, or command in the template.

2. STARTING VALUES (First Iteration Only):
   - CORE_UTILIZATION = 50
   - PLACE_DENSITY = 0.55
   - CORE_ASPECT_RATIO = 1.0
   - CORE_MARGIN = 1.0
   - RESYNTH_TIMING_RECOVER = 0
   - ABC_AREA = 0
   - RECOVER_POWER = 0
   - ROUTING_LAYER_ADJUSTMENT = 0.5

3. ITERATIVE ADJUSTMENT STRATEGY — follow this priority order strictly:

   STEP A — Fix core setup:

   a) If the flow SUCCEEDED:
      - Increase CORE_UTILIZATION and PLACE_DENSITY slightly to optimize for a smaller, 
        more realistic chip area.
      - Continue increasing these values across iterations as long as the flow keeps succeeding.
      - Proceed to STEP B when you believe CORE_UTILIZATION and PLACE_DENSITY have reached 
        high enough values without triggering errors.

   b) If the log contains an error like:
         "[ERROR GRT-0116] Global routing finished with congestion"
      This means PLACE_DENSITY is too high for the current CORE_UTILIZATION. Choose ONE of:
      - Keep CORE_UTILIZATION the same and DECREASE PLACE_DENSITY, OR
      - Decrease CORE_UTILIZATION and keep PLACE_DENSITY the same.

   c) If the log contains an error like:
         "[ERROR GPL-0302] Consider increasing the target density or re-floorplanning with a larger core area.
          Given target density: <X>
          Suggested target density: <Y>"
      This means PLACE_DENSITY is too low. Choose ONE of:
      - Set PLACE_DENSITY to the suggested target density value <Y>, OR
      - Increase CORE_UTILIZATION to give more room to the placer.

   d) If the design area is very small (smaller than ~1000 um²):
      - Try low CORE_UTILIZATION values: 10, 15, 20.
      - If the log contains an error like:
           "[ERROR PDN-0185] Insufficient width (<X> um) to add straps on layer..."
        This means the floorplan is too small for power straps. ONLY in this case, increase CORE_MARGIN.
        Try values: 3.0, 4.0 and dont go a lot higher.

   STEP B — Timing optimization (only after fixing core setup):

   e) If there are small timing violations (missing timing score points):
      - Set RESYNTH_TIMING_RECOVER = 1.
      - Alternatively, you can try setting ABC_AREA = 1 or RECOVER_POWER = 100,
        as these optimizations can sometimes cross-functionally resolve timing issues.
      - CRITICAL: You must NEVER set RESYNTH_TIMING_RECOVER = 1 and ABC_AREA = 1 at the same time.

   STEP C — Power and area optimization (only after fixing core setup):

   f) If there are missing power score points:
      - Set RECOVER_POWER = 100.
      - Also try RESYNTH_TIMING_RECOVER = 1
      - CRITICAL: You must NEVER set RESYNTH_TIMING_RECOVER = 1 and ABC_AREA = 1 at the same time.
   
   g) If there are missing area score points:
      - Set RESYNTH_TIMING_RECOVER = 1 and ABC_AREA = 0
      - Also try ABC_AREA = 1 and RESYNTH_TIMING_RECOVER = 0.
      - Explore both options
      - CRITICAL: You must NEVER set RESYNTH_TIMING_RECOVER = 1 and ABC_AREA = 1 at the same time.
   
   STEP D — Routing layer tuning (explore around the default):

   h) Try ROUTING_LAYER_ADJUSTMENT values around the default of 0.5:
      - Try 0.4 or 0.6 as alternatives.
      - High values ease detailed routing but risk excessive detours.
      - Low values reduce global routing failures but can complicate detailed routing.

   STEP E — Aspect ratio tuning (last resort for better area):

   i) Only after all previous steps, if area score points are still missing:
      - Try a different CORE_ASPECT_RATIO such as 1.2 or 1.3.

4. CORE_UTILIZATION: 
   - A numerical value (0-100) representing the percentage of the core area used by cells.
   - Start at 50 (see Rule 2).
   - Adjust based on feedback (see Rule 3).

5. CORE_ASPECT_RATIO: 
   - The ratio of height to width (float). 
   - Default: 1.0.
   - Only change per Rule 3i.

6. CORE_MARGIN:
   - The margin between the core area and die area, specified in microns (float). 
   - Default = 1.0.
   - ONLY increase beyond 1.0 if the PDN-0185 strap width error appears (see Rule 3d).

7. PLACE_DENSITY: 
   - The desired average placement density of cells (1.0 = dense, 0.0 = widely spread). 
   - Use a low value for faster builds and higher value for better quality of results. 
   - If a too low value is used, the placer will not be able to place all cells. 
   - A too high value can lead to excessive runtimes, even timeouts and subtle failures in the flow after placement. 
   - Start at 0.55 (see Rule 2).
   - Adjust based on feedback (see Rule 3).

8. RESYNTH_TIMING_RECOVER:
   - Enables re-synthesis for timing optimization.
   - Can also be cross-explored for area and power fixes.
   - Default = 0.
   - Set to 1 ONLY per Rule 3e above.

9. ABC_AREA
   - Targets synthesis for area optimizations.
   - Can also be cross-explored for timing and power fixes.
   - Default = 0.
   - Set to 1 ONLY per Rule 3g above.

10. RECOVER_POWER:
   - Specifies how many percent of paths with positive slacks can be slowed forpower savings [0-100].
   - Can also be cross-explored for area and timing fixes.
   - Default = 0.
   - Set to 100 ONLY per Rule 3f above.

11. ROUTING_LAYER_ADJUSTMENT:
   - Adjusts routing layer capacities to manage congestion and improve detailed routing. 
   - Default = 0.5.
   - Explore 0.4 and 0.6 per Rule 3h above.
"""

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
        - "synthesizability"
        - "multi_driver_conflicts"
        - "latch_prevention"
        - "timing_and_pipelining"
        - "fsm_design"
        - "lut_usage"
        - "coding_discipline"
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

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict:
        """Load the JSON file. Falls back to default if the file is missing or corrupted."""
        
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[KB] Loaded knowledge base from {self.path}")
                return data
            
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[KB] Warning: Could not parse existing KB ({e}). Starting fresh.")
        
        # Return a copy of the default schema as fallback
        return json.loads(json.dumps(self.DEFAULT_SCHEMA))

    def save(self):
        """Save the current state of the Knowledge Base to disk."""

        self.data["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self.path, "w", encoding="utf-8") as f:
            # indent = 2 to add 2 spaces in every nested line
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        
        print(f"[KB] Saved knowledge base to {self.path}")

    def increment_run(self) -> int:
        """Increment and return the total run counter in the metadata."""

        self.data["meta"]["total_runs"] += 1
        return self.data["meta"]["total_runs"]

    def increment_iterations(self, n: int = 1):
        """Track the total number of AI 'attempts' made across all time."""
        
        self.data["meta"]["total_iterations"] += n

    def add_lessons(self, new_lessons: list[dict], design: str, run: int) -> int:
        """
        Merge new lessons into the KB, prevent duplicates, and handle pruning.
        Return the number of unique lessons actually added.
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

    def as_lm_prompt(self, design_name: str = None, filter_by_design: bool = False) -> str:
        """
        Render the KB as a clean, structured block suitable for injection into the LM's prompt.
        Return a formatted string containing all lessons by category or an empty string if no lessons exist.
        """

        all_lessons = self.data["lessons"]
        if not any(all_lessons.values()):
            return ""

        # Determine header based on filter by design
        if filter_by_design and design_name:
            scope_label = f"design '{design_name}'"
            header_note = f"These rules were distilled from previous runs on {scope_label}."
        else:
            scope_label = "all designs"
            header_note = "These rules were distilled from previous generation runs."

        lines = [
            "--- ACCUMULATED KNOWLEDGE BASE ---",
            header_note,
            "Follow them strictly as they encode hard-won lessons.",
            ""
        ]

        has_any = False

        for category, entries in sorted(all_lessons.items()):
            if not entries:
                continue

            # Apply design filter if requested
            if filter_by_design and design_name:
                filtered = [e for e in entries if e.get("source_design") == design_name]
            else:
                filtered = entries

            if not filtered:
                continue

            has_any = True
            # Format category names for readability
            lines.append(f"[{category.upper().replace('_', ' ')}]")

            # Number the lessons 1, 2, 3... under each category
            for idx, entry in enumerate(filtered, 1):
                lines.append(f"  {idx}. {entry['lesson']}")
            lines.append("")

        if not has_any:
            return ""

        return "\n".join(lines)

    def summary(self) -> str:
        """Return a status string of the current KB state."""

        meta = self.data["meta"]
        total = sum(len(v) for v in self.data["lessons"].values())
        return (f"Runs: {meta['total_runs']} | "
                f"Iterations: {meta['total_iterations']} | "
                f"Lessons: {total} across {len(self.data['lessons'])} categories")

# --- SIGNATURES ---

class TBGenerator(dspy.Signature):
    """
    You are an expert Design Verification Engineer. Create a self-checking 
    Verilog-2001 testbench for the given YAML specification and simulation rules.
    """
    yaml_spec        = dspy.InputField(desc="Hardware specification in YAML format")
    module_signature = dspy.InputField(desc="DUT (Device Under Test) signature")
    simulation_rules = dspy.InputField(desc="MANDATORY simulation rules")
    previous_code    = dspy.InputField(desc="Testbench Verilog code from the last iteration (if any)")
    feedback         = dspy.InputField(desc="Errors from Icarus Verilog or logic mismatches")
    testbench_code   = dspy.OutputField(desc="Complete Verilog-2001 testbench, no explanation, no markdown, no timescale")

class TBValidator(dspy.Signature):
    """
    You are an expert Design Verification Engineer. 
    Audit the Generated Verilog-2001 Testbench against the YAML Specification 
    and the Mandatory Validation Checklist.
    """
    yaml_spec        = dspy.InputField(desc="Hardware specification in YAML format")
    testbench_code   = dspy.InputField(desc="Testbench code to be validated")
    validation_rules = dspy.InputField(desc="Mandatory validation checklist and TB generation rules")
    validation       = dspy.OutputField(desc=("MUST start with 'Match' or 'Mismatch'. "
                                              "If 'Mismatch', provide a concise list ONLY of the missing edge cases, "
                                              "logical errors, or rule violations found. If 'Match', leave the list empty."))

class SDCGenerator(dspy.Signature):
    """
    You are an expert ASIC timing constraints engineer. 
    Given a hardware YAML specification, an SDC template, and SDC generation rules,
    produce a correct and complete Synopsys Design Constraints (SDC) file for Yosys synthesis.
    """
    yaml_spec    = dspy.InputField(desc="Hardware functional requirements in YAML format")
    sdc_rules    = dspy.InputField(desc="MANDATORY SDC generation rules")
    sdc_template = dspy.InputField(desc="Available SDC templates to fill in (Sequential vs Combinational)")
    previous_sdc = dspy.InputField(desc="SDC file from the last iteration (if any)")
    feedback     = dspy.InputField(desc="OpenROAD Flow Scripts errors from previous SDC attempts, or 'Initial attempt'.")
    sdc_content  = dspy.OutputField(desc="Complete SDC file content, no markdown, no explanation")

class RTLGenerator(dspy.Signature):
    """
    You are an expert RTL engineer. Transform a YAML hardware specification 
    into high-quality, synthesizable Verilog-2001 code according to the 
    provided YAML specification and Hardware Rules.
    """
    yaml_spec        = dspy.InputField(desc="Hardware functional requirements in YAML format")
    module_signature = dspy.InputField(desc="Exact Verilog module port declaration")
    rtl_rules        = dspy.InputField(desc="MANDATORY hardware rules")
    knowledge_base   = dspy.InputField(desc="Accumulated lessons from previous runs. Follow these as additional mandatory rules.")
    previous_code    = dspy.InputField(desc="Verilog code from the last iteration (if any)")
    feedback         = dspy.InputField(desc="Simulation/Synthesis error logs to fix")
    rtl_code         = dspy.OutputField(desc="Complete SYNTHESIZABLE Verilog-2001 code, no explanation, no markdown")

class RTLValidator(dspy.Signature):
    """
    You are a senior RTL verification lead. Audit the generated Verilog-2001 RTL
    against the YAML specification and the Mandatory Validation Checklist.
    """
    yaml_spec        = dspy.InputField(desc="Hardware functional requirements in YAML format.")
    module_signature = dspy.InputField(desc="Exact Verilog module port declaration.")
    rtl_code         = dspy.InputField(desc="RTL code to be audited.")
    validation_rules = dspy.InputField(desc="Mandatory validation checklist and RTL generation rules.")
    feedback         = dspy.InputField(desc="Simulation/synthesis error logs from the evaluator.")
    audit_report     = dspy.OutputField(desc=("MUST start with exactly 'Match' or 'Mismatch' on the first line. "
                                              "If 'Mismatch', provide a concise numbered list of concrete fixes needed. "
                                              "Format each item as: '<issue description> -> FIX: <exact corrective action>. "
                                              "If 'Match', leave the list empty. Be specific and actionable."))

class RTLKnowledgeDistiller(dspy.Signature):
    """
    You are a senior RTL architect whose job is to distill portable, reusable lessons
    from a completed verification run. These lessons will be stored in a JSON knowledge base
    and fed to a smaller, less capable language model (SLM) as extra rules.

    CRITICAL REQUIREMENTS for every lesson:
    - Plain imperative English. Specific and actionable.
    - Each lesson must stand alone, no assumed context from the run.
    - Do NOT duplicate rules already in the existing knowledge base.
    - Focus on non-obvious lessons: things that caused simulation/synthesis failures,
      required multiple retries, or were caught by the Validator but missed by the SLM.
    - Categorise into exactly one of:
        synthesizability | multi_driver_conflicts  | latch_prevention | timing_and_pipelining | 
        fsm_design | lut_usage | coding_discipline | general_strategy
      You MAY introduce a new category if none fits.
    """
    design_name      = dspy.InputField(desc="Name of the hardware module that was generated.")
    yaml_spec        = dspy.InputField(desc="Hardware functional requirements in YAML format.")
    run_transcript   = dspy.InputField(desc=("Full transcript of the generation run: all iterations, RTL code, simulation logs, synthesis logs, and validator reports."))
    existing_kb      = dspy.InputField(desc="Current knowledge base content (to avoid duplicates).")
    final_status     = dspy.InputField(desc="'Success' or 'Fail', and the number of iterations taken.")
    new_lessons_json = dspy.OutputField(desc=("A JSON array of lesson objects. Each object MUST have exactly two keys: "
                                              "'category' (string) and 'lesson' (string). "
                                              "Output ONLY the raw JSON array. No markdown fences, no explanation, no preamble. "
                                              "If there are no new lessons to add, output an empty array: []"))

class ConfigMKGenerator(dspy.Signature):
    f"""
    You are an expert OpenROAD Flow Scripts (ORFS) configuration engineer.
    Given a hardware YAML specification, a Verilog RTL file, a config.mk template, and config generation 
    rules, produce a correct and complete config.mk file for the OpenROAD physical design flow.

    You are operating in an iterative optimization loop with a maximum of {MAX_PHYSICAL_ITERS} iterations. 
    On each iteration you must:
    - Carefully read the feedback field, which contains either flow errors or performance metrics from the previous run.
    - Follow the config rules strictly and in priority order to decide which parameters to adjust.
    - Use the previous_config field to understand what values were used last and adjust from there.
    - Your goal is to incrementally improve the design's power, performance and area metrics across iterations.
    - Even if the evalaution report scores 100/100 try to further optimize the design by changing the config.mk file.
    """
    iteration_count = dspy.InputField(desc="Current iteration number and maximum iterations")
    yaml_spec       = dspy.InputField(desc="Hardware functional requirements in YAML format")
    config_rules    = dspy.InputField(desc="MANDATORY config.mk generation rules")
    rtl_code        = dspy.InputField(desc="Synthesized Verilog-2001 RTL")
    config_template = dspy.InputField(desc="config.mk template to fill in")
    previous_config = dspy.InputField(desc="config.mk file from the last iteration (if any)")
    feedback        = dspy.InputField(desc="Errors or Performance Metrics from previous attempts.")
    config_content  = dspy.OutputField(desc="Complete config.mk file content, no markdown, no explanation")

# --- MODULES (The Execution Flows) ---

class TBFlow(dspy.Module):
    def __init__(self):
        super().__init__()
        self.tb_generator = dspy.ChainOfThought(TBGenerator)
        self.tb_validator = dspy.ChainOfThought(TBValidator)
        self.feedback = None
        self.previous_tb = None

    def _clean_tb(self, raw: str) -> str:
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

    def forward(self, yaml_spec: str, module_signature: str, module_id: str) -> tuple[Path, str]:
        global TB_GEN_ITER

        # Check for cross-flow feedback before starting the iteration loop
        if self.feedback:
            feedback = self.feedback
            self.feedback = None  # Consume the feedback
        else:
            feedback = "Initial attempt. Ensure strict specification compliance and PASS/FAIL reporting."

        if self.previous_tb:
            previous_tb = self.previous_tb
            self.previous_tb = None  # Consume the TB
        else:
            previous_tb = "None (First Attempt)"

        # Start iterative process from the specifed starting iteration.
        while TB_GEN_ITER <= MAX_TB_GEN_ITERS:
            # Use the current counter value for 
            # logs/files, then increment immediately
            i = TB_GEN_ITER
            TB_GEN_ITER += 1

            print(f"\n{C}[TB GEN] Iteration {i}/{MAX_TB_GEN_ITERS}...{R}")
            
            # Generate the TB
            with dspy.context(lm=TB_GENERATOR_LM):
                result = self.tb_generator(
                    yaml_spec=yaml_spec, 
                    module_signature=module_signature, 
                    simulation_rules=TB_GENERATOR_RULES, 
                    previous_code=previous_tb, 
                    feedback=feedback
                )

            # Print lm input prompt, reasoning and output
            TB_GENERATOR_LM.inspect_history(n=1)
            
            # Save TB
            final_tb =  self._clean_tb(raw=result.testbench_code)
            previous_tb = final_tb
            save_file(TB_GEN_DIR, f"{module_id}_ITER_{i}_tb.v", final_tb)
            tb_path = save_file(TB_GEN_DIR, f"{module_id}_tb.v", final_tb)
            
            # Create the Stub as a temporary file
            with tempfile.NamedTemporaryFile(suffix=".v", mode="w", delete=True) as tmp_stub:
                stub_content = f"`timescale 1ns/1ps\n{module_signature}\nendmodule"
                tmp_stub.write(stub_content)
                tmp_stub.flush() # Ensure content is written to disk before iverilog runs

                # Syntax check with Icarus Verilog
                # tmp_stub.name provides the absolute path to the temp file
                check_cmd = ["iverilog", "-Wall", "-g2001", "-o", "tb_check.out", tb_path, tmp_stub.name]
                process = subprocess.run(check_cmd, capture_output=True, text=True)
                full_output = process.stdout + process.stderr

                # Cleanup the compiled output if it exists
                if Path("tb_check.out").exists():
                    Path("tb_check.out").unlink()

                print("\nCompiling testbench with Icarus Iverilog...")

                # Check for hard failure or warnings
                if process.returncode != 0 or full_output:
                    if process.returncode != 0:
                        print("Compilation failed.\n")
                    else:
                        print("Compilation succeeded with warnings.\n")
                    print(full_output)
                    print(f"{RD}❌ Testbench syntax error/warning. Feeding back errors to agent...{R}")
                    feedback = f"Fix the following testbench syntax errors/warnings:\n{full_output}"

                # Check if PASS string exists
                elif "PASS" in final_tb:
                    print("Compilation succeeded.")
                    print(f"\n{G}{B}✅ Success! Testbench syntax verified after {i} iterations.{R}")

                    print(f"\n{C}[TB VALIDATE] Checking logic and protocol...{R}")
                    with dspy.context(lm=TB_VALIDATOR_LM):
                        result = self.tb_validator(
                            yaml_spec=yaml_spec, 
                            testbench_code=final_tb, 
                            validation_rules=TB_VALIDATOR_RULES
                        )

                    # Print lm input prompt, reasoning and output
                    TB_VALIDATOR_LM.inspect_history(n=1)

                    if "Mismatch" in result.validation:
                        print(f"{RD}❌ Testbench logic mismatch found by Validator. Feeding back errors to Generator...{R}")
                        feedback = f"The Testbench Validator found issues in your previous code. Fix them.\nIssues:\n{result.validation}"
                    else:
                        print(f"{G}{B}✅ Success! Testbench validated after {i} iterations.{R}")
                        return tb_path, "PASS"
                
                else:
                    print("Compilation succeeded.")
                    print(f"\n{RD}❌ Testbench missing correct PASS message. Feeding back errors to agent...{R}")
                    feedback = (
                        "TB compiled, but is missing the correct printing style.\n"
                        "Every comparison must print: "
                        "TEST: [PASS/FAIL] | Inputs: [Name=Value...] | Expected: [Value] | Output: [Value]"
                    )

        print(f"\n{RD}{B}Reached max iterations ({MAX_TB_GEN_ITERS}) without passing TB generation flow.{R}")

        return tb_path, "Fail"

class SDCFlow(dspy.Module):
    def __init__(self):
        super().__init__()
        self.sdc_generator = dspy.ChainOfThought(SDCGenerator)
        self.feedback = None
        self.previous_sdc = None

    def _clean_sdc(self, raw: str) -> str:
        """Extract and sanitize SDC from raw LM output."""

        # Strip any opening fence line (e.g. ```sdc, ```tcl, ```)
        raw = re.sub(r"^```\w*\n", "", raw.strip())
        # Strip any closing fence line
        raw = re.sub(r"\n```$", "", raw.strip())

        return raw.strip()

    def _verify_sdc_structure(self, template: str, generated: str) -> list[str]:
        """
        Compare the generated SDC against the template.
        Return a list of error messages for any missing, illegally 
        modified, or extra lines not present in the template.
        """

        # Remove placeholders from template to get the "static" parts
        static_template = re.sub(r"<[^>]+>", "", template).splitlines()
        static_template_lines = [l.strip() for l in static_template if l.strip()]

        generated_lines = [l.strip() for l in generated.splitlines() if l.strip()]

        issues = []

        # Missing or modified template lines
        for t_line in static_template_lines:
            if not any(t_line in g_line for g_line in generated_lines):
                issues.append(f"\nMissing or modified template line: '{t_line}'")

        # Extra lines not derived from the template.
        # Build a set of all non-placeholder template tokens for reverse lookup.
        for g_line in generated_lines:
            # Check if this generated line is "covered" by any template line.
            # A line is covered if it matches a static template line,
            # or if it corresponds to a placeholder-containing template line.
            covered = False

            for t_line in static_template_lines:
                if t_line in g_line or g_line in t_line:
                    covered = True
                    break

            # Also allow lines that correspond to placeholder lines in the original template
            if not covered:
                for raw_t_line in template.splitlines():
                    raw_stripped = raw_t_line.strip()
                    if not raw_stripped:
                        continue

                    # If the template line had a placeholder, it's a "variable" line.
                    # Accept the generated version loosely by checking the non-placeholder prefix.
                    static_prefix = re.split(r"<[^>]+>", raw_stripped)[0].strip()
                    if static_prefix and g_line.startswith(static_prefix):
                        covered = True
                        break

            if not covered:
                issues.append(f"\nExtra line not in template: '{g_line}'")

        return issues

    def forward(self, yaml_spec: str, module_id: str, orfs_design_path: Path) -> tuple[Path, str]:
        global SDC_GEN_ITER

        # Check for cross-flow feedback before starting the iteration loop
        if self.feedback:
            feedback = self.feedback
            self.feedback = None  # Consume the feedback
        else:
            feedback = "Initial attempt."

        if self.previous_sdc:
            previous_sdc = self.previous_sdc
            self.previous_sdc = None  # Consume the SDC
        else:
            previous_sdc = "None (First Attempt)"

        # Load SDC templates
        with open(SDC_SEQ_TEMPLATE, "r") as f:
            sdc_seq_template = f.read()

        with open(SDC_COMB_TEMPLATE, "r") as f:
            sdc_comb_template = f.read()

        # Replace MODULE_NAME with the actual module name provided in the spec
        sdc_seq_template  = sdc_seq_template.replace("<MODULE_NAME>", module_id)
        sdc_comb_template = sdc_comb_template.replace("<MODULE_NAME>", module_id)

        # Pass BOTH templates to the agent
        template_options = f"OPTION A (Sequential):\n{sdc_seq_template}\n\nOPTION B (Combinational):\n{sdc_comb_template}"

        # Start iterative process from the specifed starting iteration.
        while SDC_GEN_ITER <= MAX_SDC_GEN_ITERS:
            # Use the current counter value for 
            # logs/files, then increment immediately
            i = SDC_GEN_ITER
            SDC_GEN_ITER += 1

            print(f"\n{C}[SDC GEN] Iteration {i}/{MAX_SDC_GEN_ITERS}...{R}")

            # Generate the SDC with current feedback
            with dspy.context(lm=SDC_GENERATOR_LM):
                result = self.sdc_generator(
                    yaml_spec=yaml_spec, 
                    sdc_rules=SDC_GENERATOR_RULES, 
                    sdc_template=template_options, 
                    previous_sdc=previous_sdc, 
                    feedback=feedback
                )

            # Print lm input prompt, reasoning and output
            SDC_GENERATOR_LM.inspect_history(n=1)

            # Clean the SDC
            final_sdc = self._clean_sdc(raw=result.sdc_content)
            previous_sdc = final_sdc

            # Save the SDC
            save_file(orfs_design_path, "constraint.sdc", final_sdc)
            save_file(SDC_GEN_DIR, f"{module_id}_ITER_{i}.sdc", final_sdc)
            sdc_path = save_file(SDC_GEN_DIR, f"{module_id}.sdc", final_sdc)

            # Determine which template to verify against by looking at the agent's output
            if "create_clock" in final_sdc:
                chosen_template = sdc_seq_template
            else:
                chosen_template = sdc_comb_template

            # Check if agent changed sdc structure
            structure_issues = self._verify_sdc_structure(chosen_template, final_sdc)
            if structure_issues:
                print(f"\n{RD}❌ SDC Structure Violation. Feeding back errors to agent...{R}")
                print(" ".join(structure_issues))
                feedback = ("You modified the template structure! " + " ".join(structure_issues))
                continue

            # Check if agent filled in all template placeholders
            placeholders = re.findall(r"<[A-Z_]+>", final_sdc)
            if placeholders:
                print(f"\n{RD}❌ SDC generation failed due to unfilled placeholders: {placeholders}. Feeding back errors to agent...{R}")
                feedback = (
                    f"You did not fill in the following placeholders in the SDC template: {placeholders}.\n" 
                    f"Replace them with the correct values based on the YAML spec and the SDC rules provided."
                )
                continue

            print(f"\n{G}{B}✅ Success! SDC successfully generated after {i} iterations.{R}")
            return sdc_path, "Success"

        print(f"\n{RD}{B}Reached max iterations ({MAX_SDC_GEN_ITERS}) without creating a correct sdc file.{R}")
        return sdc_path, "Fail"

class RTLFlow(dspy.Module):
    def __init__(self, knowledge_base: KnowledgeBase):
        super().__init__()
        self.rtl_generator = dspy.ChainOfThought(RTLGenerator)
        self.validator = dspy.Predict(RTLValidator)
        self.distiller = dspy.Predict(RTLKnowledgeDistiller)
        self.feedback = None
        self.previous_rtl = None
        self.kb = knowledge_base
        self.transcript_lines: list[str] = []

    def _log(self, msg: str):
        """
        Append a line to the run transcript (in addition to stdout). 
        Note: transcript accumulates across multiple forward() calls on the same instance 
        intentionally, so the distiller sees the full arc including timing retries.
        """
        
        print(msg)
        self.transcript_lines.append(msg)

    def _clean_rtl(self, raw: str) -> str:
        """Extract and sanitize Verilog from raw LM output."""
        
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

        # Fix module names starting with a digit - invalid Verilog identifier.
        # e.g. "module 8bit_counter" -> "module top_8bit_counter"
        code = re.sub(r"\bmodule\s+(\d\S*)", lambda m: f"module top_{m.group(1)}", code)

        # Fix reg signals driven by assign. Verilog rule: assign drives wire, not reg.
        # Find every signal name on the left-hand side of an assign statement,
        # then if that signal is declared as reg (or output reg), flip it to wire.
        assign_targets = re.findall(r"\bassign\s+(\w+)", code)
        for name in assign_targets:
            # "reg [n:0] name" or "reg name" -> "wire [n:0] name" / "wire name"
            code = re.sub(
                rf"\breg\b(\s+(?:\[[\w\s:]+\]\s+)?){re.escape(name)}\b",
                rf"wire\1{name}", code
            )
            # "output reg [n:0] name" -> "output wire [n:0] name"
            code = re.sub(
                rf"\boutput\s+reg\b(\s+(?:\[[\w\s:]+\]\s+)?){re.escape(name)}\b",
                rf"output wire\1{name}", code
            )

        # Remove existing timescale lines (if any)
        # This matches `timescale followed by anything until the end of that line
        code = re.sub(r"`timescale\s+.*?\n", "", code).strip()

        # Manually add timescale and return the clean code
        return "`timescale 1ns/1ps\n\n" + code

    def _run_simulation(self, testbench_path: Path, design_path: Path, mode: str = "rtl", timeout: int = 10) -> str:
        """
        Run RTL or netlist simulation.
        Args:
            testbench_path: Path to the generated or provided testbench.
            design_path: Path to the RTL (.v) or Netlist (.v) file.
            mode: 'rtl' for behavioral simulation, 'post_synth' for gate-level.
            timeout: Maximum seconds allowed for the simulation execution step.
        """

        logs = []
        output_exe = f"sim_{mode}.out"
        
        self._log(f"\n--- RUNNING {mode.upper()} SIMULATION ---")

        # Construct compilation command
        if mode == "rtl":
            # Standard RTL simulation
            compile_cmd = ["iverilog", "-Wall", "-g2001", "-o", output_exe, str(design_path), str(testbench_path)]
        else:
            # Post-synthesis requires the PDK library for gate definitions
            pdk_cells = sorted(PDK_RTL_PATH.glob("*.v"))
            compile_cmd = ["iverilog", "-Wall", "-Wno-timescale", "-g2001", "-o", output_exe, 
                           *[str(p) for p in pdk_cells], str(design_path), str(testbench_path)]

        print(f"Compiling: {' '.join(compile_cmd)}")
        logs.append(f"Compiling {mode}...\n")

        # Execute compilation
        comp_proc = subprocess.run(compile_cmd, capture_output=True, text=True)
        full_output = comp_proc.stdout + comp_proc.stderr

        if comp_proc.returncode != 0:
            error_msg = f"{mode.upper()} Compilation failed:\n{full_output}"
            print(error_msg)
            logs.append(f"{error_msg}\n")
            return "".join(logs)
        elif full_output.strip():
            warning_msg = f"Compilation succeeded with warnings:\n{full_output}"
            print(warning_msg)
            logs.append(f"{warning_msg}\n")
        else:
            print("Compilation succeeded.\n")
            logs.append("Compilation succeeded.\n")

        # Execute simulation (vvp)
        print(f"Executing {mode.upper()} simulation (vvp)...")
        logs.append(f"Executing {mode.upper()} simulation (vvp)...\n")
        
        sim_proc = subprocess.Popen(
            ["vvp", output_exe],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # This merges the streams in order
            text=True,
            bufsize=1                 # Line buffering for real-time flow
        )

        # Reader thread: streams output to terminal and logs without blocking main thread
        def _reader(stream, logs):
            for line in iter(stream.readline, ""):
                print(line, end="", flush=True)
                logs.append(line)
            stream.close()

        reader_thread = threading.Thread(target=_reader, args=(sim_proc.stdout, logs))
        reader_thread.daemon = True
        reader_thread.start()
        reader_thread.join(timeout=timeout)

        if reader_thread.is_alive():
            # Simulation hung, kill it and wait for the reader to finish
            sim_proc.kill()
            sim_proc.wait()
            reader_thread.join()
            timeout_msg = f"Simulation timed out after {timeout} seconds.\n"
            print(timeout_msg)
            logs.append(timeout_msg)
        else:
            sim_proc.wait()

        # Cleanup
        if Path(output_exe).exists():
            Path(output_exe).unlink()

        return "".join(logs)
    
    def _run_yosys(self, orfs_design_path: Path) -> tuple[str, int]:
        """Run Yosys synthesis from OpenROAD."""

        print(f"\n--- RUNNING YOSYS SYNTHESIS ---")

        # Directory where the ORFS Makefile is located
        orfs_makefile_dir = Path(ORFS_DIR) / "flow"

        # Run ORFS for synthesis
        orfs_synth_proc = subprocess.run(
            ["make", "synth", f"DESIGN_CONFIG={orfs_design_path}/config.mk"],
            cwd=orfs_makefile_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # This merges the streams in order
            text=True,
        )

        return orfs_synth_proc.stdout, orfs_synth_proc.returncode

    def _extract_yosys_issues(self, yosys_log: str) -> str:
        """Extract issues from yosys log about non-synthesizable RTL."""
        
        issues = []

        # Check for "Zero Cell" optimization failure
        if re.search(r"Number of cells:\s+0\b", yosys_log):
            issues.append("--- CRITICAL SYNTHESIS ERROR: ZERO CELLS INFERRED ---")
            issues.append("ERROR: Yosys deleted your entire design (0 cells).")
            issues.append("CAUSE: Violation of RULE 1 (Multi-Driver Conflict).")
            issues.append("HINT: You likely assigned the SAME signal in different 'always' blocks or 'assign' statements.")

        # Capture Latches
        latch_matches = re.findall(r"Latch inferred for signal.*", yosys_log)
        if latch_matches:
            issues.append("--- INFERRED LATCHES DETECTED ---")
            issues.extend([m.strip() for m in latch_matches])

        # Capture Multi-driver conflicts
        driver_matches = re.findall(r"Warning: Driver-driver conflict.*", yosys_log)
        if driver_matches:
            issues.append("\n--- MULTI-DRIVER CONFLICTS ---")
            issues.extend([m.strip() for m in driver_matches])

        # Capture Non-Synthesizable Constructs
        synth_matches = re.findall(r"Warning: Ignoring call.*", yosys_log)
        if synth_matches:
            issues.append("\n--- NON-SYNTHESIZABLE CONSTRUCTS ---")
            issues.extend([m.strip() for m in synth_matches])

        # Capture Syntax/Parsing Errors
        syntax_errors = re.findall(r".*ERROR:.*", yosys_log)
        if syntax_errors:
            issues.append("\n--- FATAL SYNTAX/PARSING ERRORS ---")
            issues.extend([m.strip() for m in syntax_errors])

        return "\n".join(issues) if issues else ""

    def _run_validator(self, yaml_spec: str, module_signature: str, rtl_code: str, feedback: str) -> str:
        """Run the RTL validator and return updated feedback."""

        if SKIP_RTL_VALIDATOR:
            print("\n[RTL VALIDATOR] Skipped")
            return feedback

        self._log("\n[RTL VALIDATOR] Auditing RTL against spec and rules...")
        with dspy.context(lm=RTL_VALIDATOR_LM):
            val_res = self.validator(
                yaml_spec=yaml_spec,
                module_signature=module_signature,
                rtl_code=rtl_code,
                validation_rules=RTL_VALIDATOR_RULES,
                feedback=feedback,
            )

        self._log("\n--- RTL VALIDATOR REPORT ---")
        self._log(val_res.audit_report)
        self._log("=" * 30 + "\n")

        # Append validator findings to feedback if mismatch found
        if "MISMATCH" in val_res.audit_report.upper().splitlines()[0]:
            feedback += f"\n--- VALIDATOR ISSUES TO FIX ---\n{val_res.audit_report}"

        return feedback

    def distill_knowledge(self, specification: str, design_name: str, final_status: str, run_index: int):
        """
        Ask the KnowledgeDistiller agent to extract new lessons from
        a single run's transcript and merge them into the persistent KB.
        """

        print("\n[KB] Starting knowledge distillation...")

        # Load kb and transcript for the distiller
        existing_kb_text = self.kb.as_lm_prompt(design_name=design_name, filter_by_design=KB_FILTER_BY_DESIGN) or "(empty, this is the first run)"
        raw_transcript = "\n".join(self.transcript_lines)

        # Strip all ANSI escape sequences (colors)
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        run_transcript = ansi_escape.sub('', raw_transcript)

        try:
            with dspy.context(lm=RTL_KNOWLEDGE_DISTILLER_LM):
                distill_res = self.distiller(
                    design_name=design_name,
                    yaml_spec=specification,
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
            print(f"[KB] Distillation complete. {added} new lesson(s) added.")
            print(f"[KB] Current KB stats: {self.kb.summary()}")

        except (json.JSONDecodeError, ValueError) as e:
            print(f"[KB] Warning: Could not parse distiller output ({e}). Skipping update.")
            print(f"[KB] Raw distiller output was:\n{distill_res.new_lessons_json if 'distill_res' in dir() else 'N/A'}")

    def forward(self, yaml_spec: str, module_signature: str, module_id: str, tb_path: Path, orfs_design_path: Path, orfs_rtl_src_dir: Path, rtl_gen_dir: Path = None) -> tuple[Path, str]:
        global RTL_GEN_ITER

        # Make a local copy of the iterator for the Optimize mode, 
        # in case multiple RTLFlows are spawned
        if RTL_GEN_DSPY_MODE == "Optimize":
            # Reset for each call during optimization
            rtl_iter = 1
        else:
            rtl_iter = RTL_GEN_ITER

        rtl_gen_dir = rtl_gen_dir or RTL_GEN_DIR

        # Check for cross-flow feedback before starting the iteration loop
        timing_feedback = ""
        if self.feedback:
            feedback = self.feedback
            
            # Check for timing failure from previous attempts
            if "failed timing closure" in feedback.lower():
                timing_feedback = (
                    "CRITICAL REMINDER:\n"
                    "Check for and fix the problems listed below, but DO NOT break the timing "
                    "closure or change the hardware structure of the previously provided code!!!\n\n"
                )
                # Actual feedback will be concatenated after timing feedback

                if self.previous_rtl:
                    # Run validator on the previous RTL before entering 
                    # the RTL generation loop if timing issue occured
                    feedback = self._run_validator(
                        yaml_spec=yaml_spec,
                        module_signature=module_signature,
                        rtl_code=self.previous_rtl,
                        feedback=timing_feedback + feedback,
                    )

            self.feedback = None  # Consume the feedback
        else:
            feedback = "Initial attempt. Ensure synthesizable Verilog-2001 code."

        if self.previous_rtl:
            previous_rtl = self.previous_rtl
            self.previous_rtl = None  # Consume the RTL
        else:
            previous_rtl = "None (First Attempt)"

        # Pull current KB content to inject into LM prompt
        kb_prompt = self.kb.as_lm_prompt(design_name=module_id, filter_by_design=KB_FILTER_BY_DESIGN) if AUTONOMOUS_MODE else ""

        if AUTONOMOUS_MODE and kb_prompt:
            self._log(f"\n[KB] Injecting {len(kb_prompt.splitlines())} lines of KB knowledge into RTL Generator Agent.")
        elif AUTONOMOUS_MODE and not kb_prompt:
            self._log("\n[KB] AUTONOMOUS mode but knowledge base is empty. RTL Generator Agent will rely on rules and spec only.")
        else:
            self._log("\n[KB] TRAINING mode: KB injection disabled. Distiller will learn from this run.")

        # Locate not yet created netlist file
        netlist_path = Path(ORFS_DIR) / "flow" / "results" / ORFS_PLATFORM / module_id / "base" / "1_synth.v"

        # Start iterative process from the specifed starting iteration.
        while rtl_iter <= MAX_RTL_GEN_ITERS:
            # Use the current counter value for
            # logs/files, then increment immediately
            i = rtl_iter
            rtl_iter += 1
            RTL_GEN_ITER += 1

            self._log(f"\n{C}[RTL GEN] Iteration {i}/{MAX_RTL_GEN_ITERS}...{R}")

            if RTL_GEN_DSPY_MODE == "Optimize":

                # Run the RTL Generator using the dspy.context.lm
                result = self.rtl_generator(
                    yaml_spec=yaml_spec, 
                    module_signature=module_signature, 
                    rtl_rules=RTL_GENERATOR_RULES,
                    knowledge_base=kb_prompt or "(No accumulated lessons yet.)",
                    previous_code=previous_rtl,
                    feedback=feedback
                )

                # Print lm input prompt, reasoning and output
                current_lm = dspy.settings.lm
                current_lm.inspect_history(n=1)

            else:
                # Run the RTL Generator using the RTL_GENERATOR_LM
                with dspy.context(lm=RTL_GENERATOR_LM):
                    result = self.rtl_generator(
                        yaml_spec=yaml_spec, 
                        module_signature=module_signature, 
                        rtl_rules=RTL_GENERATOR_RULES,
                        knowledge_base=kb_prompt or "(No accumulated lessons yet.)",
                        previous_code=previous_rtl,
                        feedback=feedback
                    )

                # Print lm input prompt, reasoning and output
                RTL_GENERATOR_LM.inspect_history(n=1)

            self.transcript_lines.append(
                f"\n--- RTL GENERATOR REASONING (Iteration {i}) ---\n"
                f"{result.reasoning}\n"
                f"--- END REASONING ---\n"
            )

            final_rtl = self._clean_rtl(raw=result.rtl_code)
            previous_rtl = final_rtl

            self.transcript_lines.append(
                f"\n--- VERILOG CODE (Iteration {i}) ---\n"
                f"{final_rtl}\n"
                f"--- END CODE (Iteration {i}) ---\n"
            )

            # Save RTL into ORFS project and RTL generatation directory
            save_file(orfs_rtl_src_dir, f"{module_id}.v", final_rtl)
            save_file(rtl_gen_dir, f"{module_id}_ITER_{i}.v", final_rtl)
            rtl_path = save_file(rtl_gen_dir, f"{module_id}.v", final_rtl)

            # Run RTL simulation
            full_log = self._run_simulation(testbench_path=tb_path, design_path=rtl_path, mode="rtl")
            self.transcript_lines.append(full_log)

            # Check RTL simulation success criteria
            if "PASS" in full_log.upper() and "FAIL" not in full_log.upper():
                self._log(f"\n{G}{B}✅ Success! RTL verified after {i} iterations.{R}")
                self._log(f"\n🚀 Starting synthesis for: {module_id}...")

                # Run synthesis and capture log and issues
                yosys_log, yosys_return_code = self._run_yosys(orfs_design_path=orfs_design_path)
                save_file(rtl_gen_dir, f"{module_id}_yosys_ITER_{i}.log", yosys_log)
                synthesis_issues = self._extract_yosys_issues(yosys_log=yosys_log)
                self.transcript_lines.append(synthesis_issues)

                # Check Yosys success criteria
                if yosys_return_code != 0 or synthesis_issues:
                    self._log(f"{RD}❌ Synthesis issues found. Feeding back errors to agent...{R}")
                    feedback = timing_feedback
                    feedback += (
                        f"Synthesis failed.\n"
                        f"Your code must be pure synthesizable Verilog-2001.\n"
                        f"CRITICAL: Fix the following issues by strictly adhering to the RTL rules.\n"
                        f"{synthesis_issues if synthesis_issues else '--- ISSUES IDENTIFIED ---\nFatal Syntax Error in RTL.'}"
                    )

                else:
                    self._log(f"{G}{B}✅ Success! Netlist saved at: {netlist_path}{R}")

                    # Run post-synthesis simulation
                    post_log = self._run_simulation(testbench_path=tb_path, design_path=netlist_path, mode="post_synth")
                    self.transcript_lines.append(post_log)

                    # Check post synthesis simulation success criteria
                    if "PASS" in post_log.upper() and "FAIL" not in post_log.upper():
                        self._log(f"\n{G}{B}✅ Success! Post-synthesis simulation passed after {i} iterations.{R}")
                        # Return as prediction for the MiPROv2 optimization
                        return dspy.Prediction(rtl_path=rtl_path, status="Success")
                    else:
                        self._log(f"\n{RD}❌ Post-synthesis simulation failed. Feeding back errors to agent...{R}")
                        feedback = timing_feedback
                        feedback += (
                            f"Your RTL actually PASSED the functional testbench simulation.\n"
                            f"HOWEVER, the Post-Synthesis (Gate-Level) simulation FAILED.\n"
                            f"This usually means your Verilog syntax caused Synthesizer to misinterpret the logic.\n"
                            f"CRITICAL: Recheck your previous Verilog-2001 code with respect to the RTL rules.\n"
                            f"--- POST SYNTHESIS SIMULATION LOGS ---\n{post_log}"
                        )

            else:
                # Update feedback for the next RTL generation iteration
                self._log(f"\n{RD}❌ RTL simulation failed. Feeding back errors to agent...{R}")
                feedback = timing_feedback
                feedback += (
                    f"RTL simulation failed.\n"
                    f"Fix the issues provided in EVALUATION LOGS while strictly following the RTL rules.\n"
                    f"--- EVALUATION LOGS ---\n{full_log}"
                )

            # RTL VALIDATOR - LLM (runs after every failed iteration)
            feedback = self._run_validator(
                yaml_spec=yaml_spec,
                module_signature=module_signature,
                rtl_code=final_rtl,
                feedback=feedback,
            )

        self._log(f"\n{RD}{B}Reached max iterations ({MAX_RTL_GEN_ITERS}) without passing RTL generation flow.{R}")
        # Return as prediction for the MiPROv2 optimization
        return dspy.Prediction(rtl_path=rtl_path, status="Fail")    

class PhysicalFlow(dspy.Module):
    def __init__(self):
        super().__init__()
        self.config_generator = dspy.ChainOfThought(ConfigMKGenerator)
        self.feedback = None
        self.previous_config = None

    def _clean_config(self, raw: str) -> str:
        """Extract and sanitize config.mk from raw LM output."""

        # Strip any opening fence line (e.g. ```makefile, ```mk, ```)
        raw = re.sub(r"^```\w*\n", "", raw.strip())
        # Strip any closing fence line
        raw = re.sub(r"\n```$", "", raw.strip())

        return raw.strip()
    
    def _run_openroad_flow(self, orfs_design_path: Path) -> tuple[str, int]:
        """Run OpenROAD flow. """

        # Directory where the ORFS Makefile is located
        orfs_makefile_dir = Path(ORFS_DIR) / "flow"
        start_time = time.time()

        print("\nCleaning previous ORFS results...")
        
        # Run make clean_all
        subprocess.run(
            ["make", "clean_all", f"DESIGN_CONFIG={orfs_design_path}/config.mk"], 
            cwd=orfs_makefile_dir, 
            stderr=subprocess.DEVNULL, # Ignore stderr clean logs
            stdout=subprocess.DEVNULL  # Ignore stdout clean logs
        )

        print(f"\n--- RUNNING OPENROAD FLOW ---")
        
        # Run ORFS
        orfs_proc = subprocess.run(
            ["make", f"DESIGN_CONFIG={orfs_design_path}/config.mk"],
            cwd=orfs_makefile_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # This merges the streams in order
            text=True,
        )

        print(f"⏱️  OpenROAD flow execution time: {time.time() - start_time:.2f} seconds")
        return orfs_proc.stdout, orfs_proc.returncode
    
    def _extract_orfs_issues(self, orfs_log: str, context_lines: int = 3) -> str:
        """Extract relevant issues from ORFS log with context lines around each error."""

        lines = orfs_log.splitlines()
        issues = []
        added_indices = set()   # To handle duplicate lines

        # Find all lines containing "error" (case-insensitive) ignoring makefile errors
        error_indices = [
            i for i, line in enumerate(lines)
            if re.search(r"error", line, re.IGNORECASE) and "***" not in line
        ]

        if error_indices:
            issues.append("--- LOGGED ERRORS ---")
            
            for idx in error_indices:
                # Capture +- context_lines arround error    
                start = max(0, idx - context_lines)
                end = min(len(lines) - 1, idx + context_lines)

                for i in range(start, end + 1):
                    # Add the context lines only if they
                    # are not makefile errors or duplicates
                    if i not in added_indices and "***" not in lines[i]:
                        issues.append(lines[i].strip())
                        added_indices.add(i)
        else:
            # Fall back to Makefile fatal errors ('***' lines) with context
            fatal_indices = [
                i for i, line in enumerate(lines)
                if "***" in line
            ]

            if fatal_indices:
                issues.append("--- MAKEFILE SYNTAX & FATAL ERRORS ---")

                for idx in fatal_indices:
                    # Cpature +- context_lines arround error    
                    start = max(0, idx - context_lines)
                    end = min(len(lines) - 1, idx + context_lines)
                    
                    for i in range(start, end + 1):
                        if i not in added_indices:
                            issues.append(lines[i].strip())
                            added_indices.add(i)

        # If any issues were found, also capture the design area line (up to u^2)
        if issues:
            for line in lines:
                match = re.search(r"(Design area\s+[\d\.]+\s+u\^2)", line, re.IGNORECASE)
                if match:
                    issues.append("\n--- DESIGN AREA INFO ---")
                    issues.append(match.group(1))
                    break

        return "\n".join(issues) if issues else ""

    def _run_openroad_evaluation(self, orfs_results_dir: Path) -> tuple[str, int]:
        """Run evaluate_openroad.py on the generated OpenROAD .odb and .sdc files."""

        problem_number = re.search(r'p(\d+)', DESIGN).group(1)

        # Construct evaluation command using -u flag 
        # to force unbuffered output from the child script
        eval_cmd = [
            "python3",
            "-u",
            str(EVALUATION_DIR / "evaluate_openroad.py"),
            "--odb",
            str(orfs_results_dir / "6_final.odb"),
            "--sdc",
            str(orfs_results_dir / "6_final.sdc"),
            "--flow_root",
            str(ORFS_DIR),
            "--problem",
            problem_number,
        ]

        print(f"\n--- RUNNING OPENROAD EVALUATION ---")
        print(" ".join(eval_cmd))

        process = subprocess.Popen(
            eval_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # This merges the streams in order
            text=True,
            bufsize=1,                # Line buffering for real-time flow
        )

        logs = []
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                # Print to terminal immediately
                print(line, end="", flush=True)
                # Append to the log
                logs.append(line)
            process.stdout.close()

        process.wait()
        print()

        return "".join(logs), process.returncode

    def _copy_openroad_results(self, orfs_results_dir: Path, rtl_path: Path) -> Path:
        """Copy the OpenROAD results and RTL into the local solutions folder."""
        
        print(f"\n--- COPPING RESULTS TO SOLUTIONS DIRECTORY ---")

        # Copy final OpenROAD results
        for filename in ["6_final.odb", "6_final.sdc"]:            
            src = orfs_results_dir / filename
            if not src.exists():
                raise FileNotFoundError(f"OpenROAD result file not found: {src}")           
            dest = SOLUTIONS_DIR / filename
            shutil.copy(src, dest)
            print(f"✨ Copied {src} -> {dest}")

        # Copy final RTL
        rtl_dest = SOLUTIONS_DIR / Path(rtl_path).name
        shutil.copy(rtl_path, rtl_dest)
        print(f"✨ Copied {rtl_path} -> {rtl_dest}")

    def forward(self, yaml_spec: str, module_id: str, rtl_path: Path, orfs_design_path: Path, orfs_results_dir: Path) -> tuple[Path, str, str]:
        global PHYSICAL_ITER
        
        # Check for cross-flow feedback before starting the iteration loop       
        if self.feedback:
            feedback = self.feedback
            self.feedback = None  # Consume the feedback
        else:
            feedback = "Initial attempt."

        if self.previous_config:
            previous_config = self.previous_config
            self.previous_config = None  # Consume the config
        else:
            previous_config = "None (First Attempt)"

        # Track the best run
        best_score = -1.0
        best_config_path = None

        # Load config.mk template
        with open(CONFIG_TEMPLATE, "r") as f:
            config_template = f.read()

        config_template = config_template.replace("<DESIGN_NAME>", module_id)
        config_template = config_template.replace("<PLATFORM>", ORFS_PLATFORM)

        # Load RTL code
        with open(rtl_path, "r") as f:
            rtl_code = f.read()

        # Start iterative process from the specifed starting iteration.
        # Try to find the best config file for the best score.
        while PHYSICAL_ITER <= MAX_PHYSICAL_ITERS:
            # Use the current counter value for
            # logs/files, then increment immediately
            i = PHYSICAL_ITER
            PHYSICAL_ITER += 1

            if CONFIG_PATH is None:
                print(f"\n{C}[CONFIG GEN] Iteration {i}/{MAX_PHYSICAL_ITERS}...{R}")

                # Generate the config.mk with current feedback
                with dspy.context(lm=CONFIG_MK_GENERATOR_LM):
                    result_config = self.config_generator(
                        iteration_count = f"Iteration {i}/{MAX_PHYSICAL_ITERS}", 
                        yaml_spec=yaml_spec,
                        config_rules=CONFIG_GENERATOR_RULES, 
                        rtl_code=rtl_code,
                        config_template=config_template,
                        previous_config=previous_config, 
                        feedback=feedback,
                    )

                # Print lm input prompt, reasoning and output
                CONFIG_MK_GENERATOR_LM.inspect_history(n=1)

                # Save config.mk
                final_config = self._clean_config(raw=result_config.config_content)
                iter_config_path = save_file(PHYSICAL_DIR, f"{module_id}_ITER_{i}_config.mk", final_config)
                config_path = save_file(PHYSICAL_DIR, f"{module_id}_config.mk", final_config)
            else:
                # Use already created config.mk
                print(f"\nConfig generation flow skipped.\nUsing: {CONFIG_PATH}")
                final_config = Path(CONFIG_PATH).read_text()
                iter_config_path = final_config
            
            save_file(orfs_design_path, "config.mk", final_config)
            previous_config = final_config

            # Check if agent filled in all template placeholders
            placeholders = re.findall(r"<[A-Z_]+>", final_config)
            if placeholders:
                print(f"\n{RD}❌ Config generation failed due to unfilled placeholders: {placeholders}. Feeding back errors to agent...{R}")
                feedback = (
                    f"You did not fill in the following placeholders in the config.mk template: {placeholders}.\n"
                    f"Replace them with the correct values from the YAML spec and config rules provided."
                )
                continue

            # Run OpenROAD flow
            orfs_log, orfs_error_code = self._run_openroad_flow(orfs_design_path=orfs_design_path)
            save_file(PHYSICAL_DIR, f"{module_id}_ORFS_ITER_{i}.log", orfs_log)
            orfs_issues = self._extract_orfs_issues(orfs_log=orfs_log)

            # Check ORFS success criteria
            if orfs_error_code != 0 or orfs_issues:
                if CONFIG_PATH is not None:
                    # If a config is provided stop in one iteration
                    print(f"{RD}❌ OpenROAD flow failed.{R}")
                    return CONFIG_PATH, "Fail", ""

                print(f"{RD}❌ OpenROAD flow failed. Feeding back errors to agent...{R}")
                feedback = (
                    f"OpenROAD flow FAILED.\n"
                    f"Provide an improved config.mk file to fix the following issues:\n"
                    f"{orfs_issues if orfs_issues else 'Fatal ORFS issue in OpenROAD flow.'}"
                )
                continue

            print(f"\n{G}{B}✅ Success! OpenROAD Flow completed successfully.{R}")
            
            # Evaluate OpenRoad results
            eval_log, eval_error_code = self._run_openroad_evaluation(orfs_results_dir=orfs_results_dir)
            save_file(PHYSICAL_DIR, f"{module_id}_openroad_eval_ITER_{i}.log", eval_log)

            # Move evaluation output JSON file to physical directory
            design_base = re.sub(r'_v\d+$', '', Path(DESIGN).stem)
            json_src = Path.cwd() / f"{design_base}.json"
            if json_src.exists():
                save_file(PHYSICAL_DIR, f"{Path(DESIGN).stem}_ITER_{i}.json", json_src.read_text())
                json_src.unlink()

            if eval_error_code != 0:
                print(f"\n{RD}❌ OpenROAD evaluation failed. {B}Cannot recover from this, stopping physical flow.{R}")
                return CONFIG_PATH or config_path, "Fail", ""
            
            # Extract score
            score_match = re.search(r"Final Score: ([\d\.]+)", eval_log)
            current_score = float(score_match.group(1)) if score_match else 0.0

            # Keep track of the best score
            if current_score > best_score:
                print(f"{G}🏆 New Best Score: {current_score:.2f}/100 (Previous: {best_score:.2f}/100){R}")
                best_score = current_score
                best_config_path = iter_config_path
                # Save as the "Global Best" for the solutions folder
                self._copy_openroad_results(orfs_results_dir=orfs_results_dir, rtl_path=rtl_path)

            # Extract wns max
            wns_match = re.search(r"wns max\s+(-?[\d\.]+)", eval_log)
            if wns_match:
                wns_value = float(wns_match.group(1))
                # If WNS is significantly negative, it's likely an RTL architectural issue
                if wns_value < -0.5:
                    print(f"\n{RD}❌ Severe timing violation detected: WNS = {wns_value}ns{R}")
                    print(f"{RD}❌ Physical flow cannot fix this. Requesting RTL architectural change...{R}")
                    return CONFIG_PATH or best_config_path, "Timing Fail" , eval_log

            # If a config is provided stop in one iteration
            if CONFIG_PATH is not None:
                if eval_error_code == 0:
                    print(f"\n{G}{B}✅ Success! Single-run completed successfully for provided config.{R}")
                    return CONFIG_PATH, "Success", ""
            
            # Prepare Optimization Feedback for next iteration
            feedback = (
                f"SUCCESS: Previous run completed with Score: {current_score:.2f}/100.\n"
                f"Evaluation Details:\n{eval_log}\n\n"
                f"Try to adjust config parameters shown in config template "
                f"to improve the score further in the next iteration."
            )

        if best_score != -1.0:
            print(f"\n{G}{B}✅ Success! Physical design flow completed successfully.{R}")
            print(f"{G}{B}🏆 Best Score: {best_score:.2f} using {best_config_path}{R}")
            return CONFIG_PATH or best_config_path, "Success", ""

        print(f"\n{RD}{B}Reached max iterations ({MAX_PHYSICAL_ITERS}) without completing physical design flow.{R}")
        return CONFIG_PATH or config_path, "Fail", ""

# --- OPTIMIZATION ---

def build_rtl_trainset() -> list[dspy.Example]:
    """
    Build a DSPy trainset from RTL_TRAIN_DESIGNS.
    For each design, paths are automatically resolved from RESULTS_DIR:
        - TB  : ../results/visible/<design>/tb_gen_flow/<module_id>_tb_golden.v
        - SDC : ../results/visible/<design>/sdc_gen_flow/<module_id>_sdc_golden.sdc
        - RTL : ../results/visible/<design>/rtl_gen_flow/<module_id>_rtl_golden.v  (optional)
    """

    print("\nBuilding RTL generation flow's trainset...")

    examples = []
    for yaml_file in RTL_TRAIN_DESIGNS:

        # Load YAML spec
        yaml_path = PROBLEMS_DIR / yaml_file
        if not yaml_path.exists():
            print(f"Skipping '{yaml_file}': YAML spec not found at {yaml_path}.")
            continue

        with open(yaml_path, "r") as f:
            yaml_data = yaml.safe_load(f)

        module_id    = list(yaml_data.keys())[0]
        spec_content = yaml_data[module_id]
        yaml_spec    = yaml.dump(spec_content)
        module_sig   = spec_content.get("module_signature", "module design(...);")

        # Auto-resolve paths for this design
        design_results_dir = RESULTS_DIR.parent / Path(yaml_file).stem
        tb_path  = design_results_dir / "tb_gen_flow"  / f"{module_id}_tb_golden.v"
        sdc_path = design_results_dir / "sdc_gen_flow" / f"{module_id}_sdc_golden.sdc"
        rtl_path = design_results_dir / "rtl_gen_flow" / f"{module_id}_rtl_golden.v"  # optional

        if not tb_path.exists():
            print(f"Skipping '{yaml_file}': testbench not found at {tb_path}.")
            continue

        if not sdc_path.exists():
            print(f"Skipping '{yaml_file}': SDC not found at {sdc_path}.")
            continue

        gold_rtl = rtl_path.read_text() if rtl_path.exists() else ""

        orfs_design_path, orfs_rtl_src_dir, _ = generate_orfs_project(module_id=module_id, orfs_dir=ORFS_DIR, platform=ORFS_PLATFORM)

        # Save the SDC into the ORFS project
        path = Path(orfs_design_path) / "constraint.sdc"
        path.write_text(sdc_path.read_text())

        ex = dspy.Example(
            yaml_spec        = yaml_spec,
            module_signature = module_sig,
            module_id        = module_id,
            tb_path          = str(tb_path),
            orfs_design_path = orfs_design_path,
            orfs_rtl_src_dir = orfs_rtl_src_dir,
            rtl_gen_dir      = RESULTS_DIR.parent / Path(yaml_file).stem / "rtl_gen_flow",
        )

        # Attach gold RTL if available so MIPROv2 can use it as a labeled demo
        if gold_rtl:
            ex["rtl_code"] = gold_rtl

        # Define the inputs that the forward() function actually accepts
        ex = ex.with_inputs("yaml_spec", "module_signature", "module_id", "tb_path", "orfs_design_path", "orfs_rtl_src_dir", "rtl_gen_dir")

        examples.append(ex)
        print(f"Added '{module_id}' ({yaml_file}) to RTL trainset (gold_rtl={'yes' if gold_rtl else 'no'})")

    return examples

def make_rtl_metric(flow: RTLFlow) -> callable:
    """
    Return a DSPy-compatible metric function for RTL generation.

    Scoring:
        1.0  -> RTL simulation passed  (PASS in log, no FAIL)
        0.5  -> RTL compiled but simulation failed
        0.0  -> RTL failed to compile
    """

    def rtl_metric(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> float:
        
        rtl_gen_dir = example.get("rtl_gen_dir", None)
        module_id = example.get("module_id", None)
        tb_path_str = example.get("tb_path", "")
        
        if not rtl_gen_dir or not module_id or not tb_path_str:
            print("[Metric Error] Missing vital metadata keys in Example.")
            return 0.0
        
        # Check the testbench path
        tb_path = Path(tb_path_str)
        if not tb_path.exists():
            print(f"[Metric Error] Testbench not found: {tb_path}")
            return 0.0
        
        # Target the exact path where your RTLFlow saved this candidate's code
        rtl_path = Path(rtl_gen_dir) / f"{module_id}.v"
        if not rtl_path.exists():
            print(f"[Metric Error] RTL file not found at expected path: {rtl_path}")
            return 0.0

        # Evaluate the generated RTL file
        sim_log = flow._run_simulation(testbench_path=tb_path, design_path=rtl_path, mode="rtl")

        # Parse log output to assign score
        if "PASS" in sim_log.upper() and "FAIL" not in sim_log.upper():
            score = 1.0
        elif "Compilation failed" in sim_log:
            score = 0.0
        else:
            score = 0.5

        return score

    return rtl_metric

def run_mipro_optimization(flow: RTLFlow, trainset: list[dspy.Example]) -> dspy.Module:
    """
    Run MIPROv2 optimization on the RTL Generation Flow using the LLM as teacher.
    The optimized program is saved to 'optimized_rtl_generator.json'.
    """

    metric = make_rtl_metric(flow=flow)

    # Set up the optimizer
    teleprompter = MIPROv2(
        metric                 = metric,       # The judge used to grade the RTLs
        prompt_model           = LLM,          # The Teacher who writes new instructions
        task_model             = SLM,          # The Student who generates the code
        teacher_settings       = dict(lm=LLM), # Use LLM for the "heavy thinking" steps
        auto                   = None,         # Enable manual setting of num_candidates and num_trials
        num_candidates         = 3,            # How many different prompt versions prompt_model will generate (one of these is picked in each trial)
        max_bootstrapped_demos = 2,            # Number of successful traces to include in the prompt
        max_labeled_demos      = 1,            # Number of raw examples from trainset
        num_threads            = 1,            # Run one model instance at a time
        max_errors             = 5,            # Stop if 5 consecutive trials throw exceptions
        verbose                = True,         # Print everything to the screen
    )

    print("\n" + "="*60)
    print("Starting MIPROv2 RTL Prompt Optimization...")
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
    optimized_flow.save(str(OPTIMIZED_RTL_FLOW_PATH))
    print(f"\nOptimized RTL prompt program saved to: {OPTIMIZED_RTL_FLOW_PATH}")

    return optimized_flow

def load_optimized_rtl_flow(flow: RTLFlow) -> RTLFlow:
    """Load a previously optimized RTL prompt program into the RTLFlow."""

    if not OPTIMIZED_RTL_FLOW_PATH.exists():
        print(f"{RD}\nNo optimized RTL program found at {OPTIMIZED_RTL_FLOW_PATH}. Run optimization first.{R}")
        return flow

    flow.load(str(OPTIMIZED_RTL_FLOW_PATH))
    print(f"Loaded optimized RTL prompt program from: {OPTIMIZED_RTL_FLOW_PATH}")
    return flow

# --- UTILITIES ---

def save_file(directory: Path, filename: str, content: str) -> Path:
    """Save given content to filename into provided directory"""
    
    # Create folder if not exists
    Path(directory).mkdir(parents=True, exist_ok=True)

    # Save content
    path = Path(directory) / filename
    path.write_text(content)

    print(f"✨ File {filename} saved at : {path}")
    return path

def check_environment():
    """Verify all tools, directories, and required scripts are present."""

    global ORFS_DIR
    
    print(f"Checking environment...")
    all_ok = True

    # Check if it matches your solutions directory name
    current_folder = Path.cwd().name
    if current_folder != "solutions":
        print(f"{RD}❌ Error: Script must be executed from within the solutions directory.{R}")
        print(f"Current directory: {Path.cwd()}")
        exit(1)

    # Check for required binaries (Tools)
    tools = ["iverilog", "vvp", "yosys", "openroad", "sta"]
    for tool in tools:
        if shutil.which(tool) is None:
            print(f"{RD}❌ Error: Tool '{tool}' not found in PATH.{R}")
            all_ok = False

    # Try to auto detect ORFS_DIR
    orfs_bin = shutil.which("openroad")
    if orfs_bin is not None and ORFS_DIR is None:
        # Walk up from the binary until finding the ORFS root (identified by the 'flow' subdirectory)
        ORFS_DIR = next(
            (p for p in Path(orfs_bin).resolve().parents if (p / "flow").is_dir()),
            None
        )
        if ORFS_DIR is None:
            print(f"{RD}❌ Error: Could not locate OpenROAD-flow-scripts root. Set ORFS_DIR manually.'{R}")
            all_ok = False

    # Check for required directories
    required_dirs = [EVALUATION_DIR, PROBLEMS_DIR, RESULTS_DIR, SOLUTIONS_DIR, PDK_RTL_PATH]
    if ORFS_DIR is not None:
        required_dirs.append(ORFS_DIR)
    
    for d in required_dirs:
        d = Path(d)
        if not d.exists():
            if d == RESULTS_DIR or d == SOLUTIONS_DIR:
                try:
                    d.mkdir(parents=True)
                    print(f"{Y}⚠️  Directory '{d}' created.{R}")
                except Exception as e:
                    print(f"{RD}❌ Error: Could not create directory '{d}': {e}{R}")
                    all_ok = False
            else:
                print(f"{RD}❌ Error: Required directory not found: '{d}'{R}")
                all_ok = False

    # Check for PDK cell files
    pdk_cells = sorted(PDK_RTL_PATH.glob("*.v"))
    if not pdk_cells:
        print(f"{RD}❌ Error: No PDK .v cell files found in: '{PDK_RTL_PATH}'{R}")
        all_ok = False

    # Check for Required Files
    required_files = {
        "Design Spec": PROBLEMS_DIR / DESIGN,
        "Combinational SDC Template": SDC_COMB_TEMPLATE,
        "Sequential SDC Template": SDC_SEQ_TEMPLATE,
        "Config Template": CONFIG_TEMPLATE,
        "OpenROAD Evaluation Python Script": EVALUATION_DIR / "evaluate_openroad.py",
        "OpenROAD Evaluation TCL Script": EVALUATION_DIR / "report_metrics.tcl"
    }

    # Add override files if provided
    if TB_PATH is not None:
        required_files["Testbench Override"] = Path(TB_PATH)
    if SDC_PATH is not None:
        required_files["SDC Override"] = Path(SDC_PATH)
    if RTL_PATH is not None:
        required_files["RTL Override"] = Path(RTL_PATH)
    if CONFIG_PATH is not None:
        required_files["Config Override"] = Path(CONFIG_PATH)

    for label, path in required_files.items():
        if not Path(path).exists():
            print(f"{RD}❌ Error: {label} not found at: {path}{R}")
            all_ok = False

    # RTL trainset validation 
    if RTL_GEN_DSPY_MODE == "Optimize":
        for yaml_file in RTL_TRAIN_DESIGNS:
            yaml_path = PROBLEMS_DIR / yaml_file
            if not yaml_path.exists():
                print(f"{RD}❌ Error: Trainset spec '{yaml_file}' not found at {yaml_path}.{R}")
                all_ok = False
                continue

            # Parse YAML safely to get the internal top module_id
            try:
                with open(yaml_path, "r") as f:
                    yaml_data = yaml.safe_load(f)
                module_id = list(yaml_data.keys())[0]
            except Exception as e:
                print(f"{RD}❌ Error: Failed to parse trainset spec '{yaml_file}': {e}{R}")
                all_ok = False
                continue

            # Check for golden assets matching the structure in build_rtl_trainset
            design_results_dir = Path("../results/visible") / Path(yaml_file).stem
            tb_path = design_results_dir / "tb_gen_flow" / f"{module_id}_tb_golden.v"
            sdc_path = design_results_dir / "sdc_gen_flow" / f"{module_id}_sdc_golden.sdc"
            
            if not tb_path.exists():
                print(f"{RD}❌ Error: Golden testbench missing for trainset design '{yaml_file}' at {tb_path}{R}")
                all_ok = False
            if not sdc_path.exists():
                print(f"{RD}❌ Error: Golden SDC missing for trainset design '{yaml_file}' at {sdc_path}{R}")
                all_ok = False

    elif RTL_GEN_DSPY_MODE == "Inference":
        if not OPTIMIZED_RTL_FLOW_PATH.exists():
            print(f"{RD}❌ Error: No optimized RTL program found at {OPTIMIZED_RTL_FLOW_PATH}. Run optimization first.{R}")
            all_ok = False

    # Clean previous solution files
    if SOLUTIONS_DIR.exists():
        for f in SOLUTIONS_DIR.iterdir():
            if f.is_file():
                f.unlink()

    if not all_ok:
        print(f"\n{RD}{B}CRITICAL: Environment check failed. Fix the errors above before running.{R}")
        exit(1)
    
    validator_label = "No RTL Validator" if SKIP_RTL_VALIDATOR else "RTL Validator"

    if AUTONOMOUS_MODE:
        mode_label = f"AUTONOMOUS (KB Injection, {validator_label}, No Distiller)"
        kb_scope_label = f"Design-specific ({Path(DESIGN).stem})" if KB_FILTER_BY_DESIGN else "Global (all designs)"
    else:
        mode_label = f"SUPERVISED (No KB Injection, {validator_label}, KB Distiller)"
        kb_scope_label = "N/A (KB Distiller ON)"

    print(f"System is ready!\n")
    print(f"Starting Agentic ASIC flow for     : {C}{DESIGN}...{R}")
    print(f"RTL generation DSPy mode           : {C}{RTL_GEN_DSPY_MODE}{R}")
    print(f"RTL generation operation mode      : {C}{mode_label}{R}")
    print(f"KB  injection scope                : {C}{kb_scope_label}{R}")
    print(f"TB  Generator model selected       : {C}{TB_GENERATOR_LM.model}{R}")
    print(f"TB  Validator model selected       : {C}{TB_VALIDATOR_LM.model}{R}")
    print(f"SDC Generator model selected       : {C}{SDC_GENERATOR_LM.model}{R}")
    print(f"RTL Generator model selected       : {C}{RTL_GENERATOR_LM.model}{R}")
    print(f"RTL Validator model selected       : {C}{RTL_VALIDATOR_LM.model}{R}")
    print(f"KB  Distiller model selected       : {C}{RTL_KNOWLEDGE_DISTILLER_LM.model}{R}")
    print(f"Config MK Generator model selected : {C}{CONFIG_MK_GENERATOR_LM.model}{R}\n")

def setup_logging(filename="asic_agentic_flow.log"):
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
    f = open(filename, "w", encoding="utf-8")
    
    # Replace stdout and stderr
    sys.stdout = Logger(sys.stdout, f)
    sys.stderr = sys.stdout

def generate_orfs_project(module_id: str, orfs_dir: Path, platform: str) -> tuple[Path, Path, Path]:
    '''
    Generate the OpenROAD Flow Scripts (ORFS) project, given the top module name, the ORFS directory and the target platform.
    Returns:
        orfs_design_dir  : Path to the ORFS design directory  (.../flow/designs/<platform>/<module_id>/)
        orfs_rtl_src_dir : Path to the ORFS RTL source directory (.../flow/designs/src/<module_id>/)
        orfs_results_dir : Path to the ORFS base results directory (.../flow/results/<platform>/<module_id>/base/)
    '''

    orfs_path        = Path(orfs_dir)
    orfs_design_dir  = orfs_path / "flow" / "designs" / platform / module_id
    orfs_rtl_src_dir = orfs_path / "flow" / "designs" / "src" / module_id
    orfs_results_dir = orfs_path / "flow" / "results" / platform / module_id
    config_path      = orfs_design_dir / "config.mk"

    # Create design dir (fail if it already exists)
    orfs_design_dir.mkdir(parents=True, exist_ok=True)
    # Create verilog src dir (fail if it already exists)
    orfs_rtl_src_dir.mkdir(parents=True, exist_ok=True)
    # Results dir will be created by the flow, but ensure parent exists
    orfs_results_dir.parent.mkdir(parents=True, exist_ok=True)

    # Populate config.mk from the template and write it into the design directory.
    # This config will be used for the synthesis proccess.
    # For the physical flow a new one will be generated from the Agent.
    with open(CONFIG_TEMPLATE, "r") as f:
        config_contents = f.read()

    # Define settings for the initial config to default values
    openroad_settings = {
        "<DESIGN_NAME>": module_id,
        "<PLATFORM>": platform,
        "<UTILIZATION_PERCENTAGE>": "50",
        "<ASPECT_RATIO_FLOAT>": "1.0",
        "<CORE_MARGIN_FLOAT>": "1.0",
        "<PLACEMENT_DENSITY_FLOAT>": "0.6",
        "<ROUTING_LAYER_ADJUSTMENT_FLOAT>": "0.5",
        "<ABC_AREA_0_OR_1>": "0",
        "<RESYNTH_TIMING_RECOVER_0_OR_1>": "0",
        "<RECOVER_POWER_PERCENTAGE>": "0"
    }

    # Apply to file content
    for tag, value in openroad_settings.items():
        config_contents = config_contents.replace(tag, value)

    with open(config_path, "w") as f:
        f.write(config_contents)

    return orfs_design_dir, orfs_rtl_src_dir, orfs_results_dir / "base"

# --- MAIN EXECUTION ---

def main():
    initial_start_time = time.time()
    setup_logging()
    check_environment()

    # Load (or create) the persistent knowledge base
    kb = KnowledgeBase(KB_PATH)
    print(f"[KB] Status: {kb.summary()}")

    # Load the Spec
    design_spec = PROBLEMS_DIR / DESIGN
    with open(design_spec, "r") as f:
        yaml_data = yaml.safe_load(f)

    # Get the design in the YAML
    module_id = list(yaml_data.keys())[0]
    spec_content = yaml_data[module_id]
    signature_prompt = spec_content.get('module_signature', 'module design(...);')

    # Configure RTL generation flow based on RTL_GEN_DSPY_MODE
    if RTL_GEN_DSPY_MODE == "Optimize":
        start_time = time.time()
        trainset = build_rtl_trainset()
        if not trainset:
            print("RTL trainset is empty. Check your trainset configuration.")
            return

        rtl_flow = RTLFlow(kb)
        rtl_flow = run_mipro_optimization(flow=rtl_flow, trainset=trainset)

        print(f"⏱️  MIPROv2 optimization time: {time.time() - start_time:.2f} seconds")
        return

    print(f"\n{B}{'='*70}{R}")

    if TB_PATH is None:
        # Initialize and run the TB Flow
        print(f"\n🚀 Starting testbench generation flow for: {module_id}...")
        start_time = time.time()

        tb_flow = TBFlow()
        tb_path, status = tb_flow(yaml_spec=yaml.dump(spec_content), module_signature=signature_prompt, module_id=module_id)
        print(f"⏱️  TB generation time: {time.time() - start_time:.2f} seconds")

        # TB generation failed
        if status == "Fail":
            return
    else:
        # Use already created testbench
        tb_path = TB_PATH
        print(f"\nTestbench generation flow skipped.\nUsing: {tb_path}")

    print(f"\n{B}{'='*70}{R}")
    orfs_design_path, orfs_rtl_src_dir, orfs_results_dir = generate_orfs_project(module_id=module_id, orfs_dir=ORFS_DIR, platform=ORFS_PLATFORM)

    if SDC_PATH is None:
        # Initialize and run the SDC Flow
        print(f"\n🚀 Starting SDC generation flow for: {module_id}...")
        start_time = time.time()
        
        sdc_flow = SDCFlow()
        _, status = sdc_flow(yaml_spec=yaml.dump(spec_content), module_id=module_id, orfs_design_path=orfs_design_path)
        print(f"⏱️  SDC generation time: {time.time() - start_time:.2f} seconds")

        # SDC generation failed
        if status == "Fail":
            return
    else:
        # Use already created SDC
        print(f"\nSDC generation flow skipped.\nUsing: {SDC_PATH}")
        save_file(orfs_design_path, "constraint.sdc", Path(SDC_PATH).read_text())

    print(f"\n{B}{'='*70}{R}")

    # Initialize RTL and Physical flow
    rtl_flow_loaded = False
    rtl_flow = RTLFlow(kb)
    physical_flow = PhysicalFlow()

    # Loop: RTL Flow -> Physical Flow -> RTL Flow (if timing fails by far)
    while True:
       # Check if there is any specified feedback from physical flow results
        has_feedback = any([rtl_flow.feedback, rtl_flow.previous_rtl])

        if RTL_PATH is None or has_feedback:
            # Run the RTL Flow
            print(f"\n🚀 Starting synthesizable RTL generation flow for: {module_id}...")
            start_time = time.time()

            if RTL_GEN_DSPY_MODE == "Inference" and rtl_flow_loaded == False:
                rtl_flow = load_optimized_rtl_flow(flow=rtl_flow)
                rtl_flow_loaded = True

            prediction = rtl_flow(
                yaml_spec=yaml.dump(spec_content), 
                module_signature=signature_prompt, 
                module_id=module_id, 
                tb_path=tb_path, 
                orfs_design_path=orfs_design_path, 
                orfs_rtl_src_dir=orfs_rtl_src_dir
            )

            rtl_path = prediction.rtl_path
            status   = prediction.status
            
            print(f"⏱️  RTL generation time: {time.time() - start_time:.2f} seconds")

            # RTL generation failed
            if status == "Fail":
                break
        else:
            # Use already created RTL
            rtl_path = RTL_PATH
            print(f"\nRTL generation flow skipped.\nUsing: {rtl_path}")
            save_file(orfs_rtl_src_dir, f"{module_id}.v", Path(rtl_path).read_text())

        print(f"\n{B}{'='*70}{R}")

        # Run the Physical Flow
        print(f"\n🚀 Starting physical flow for: {module_id}...")
        start_time = time.time()

        _, status, eval_log = physical_flow(
            yaml_spec=yaml.dump(spec_content), 
            module_id=module_id, 
            rtl_path=rtl_path, 
            orfs_design_path=orfs_design_path, 
            orfs_results_dir=orfs_results_dir
        )

        print(f"⏱️  Physical flow time: {time.time() - start_time:.2f} seconds")
        print(f"\n{B}{'='*70}{R}")

        if status == "Fail":
            print(f"\n{RD}{B}❌ Failure! Agentic flow failed to complete.{R}")
            break
        elif status == "Timing Fail":
            rtl_flow.previous_rtl = Path(rtl_path).read_text()
            rtl_flow.feedback = (
                f"Your previous RTL passed functional simulation but FAILED timing closure in Physical Flow.\n"
                f"OpenROAD Evaluation Report:\n{eval_log}\n"
                f"CRITICAL: Reconstruct the RTL to meet the clock_period defined in the specification and ensure timing closure.\n"
                f"HINT: Refer to Rule 3 (TIMING & PIPELINE ARCHITECTURE)"
            )
            continue  # Trigger RTL generation again with timing feedback
        else:
            print(f"\n{G}{B}✅ Success! Agentic flow completed successfully.{R}")
            break

    # Distill knowledge from RTL generation loop
    if not AUTONOMOUS_MODE and RTL_PATH is None:
        # RTL_GEN_ITER is pre-incremented inside forward(), so subtract 1 for actual count
        rtl_flow.kb.increment_iterations(RTL_GEN_ITER - 1)

        rtl_flow.distill_knowledge(
            specification=yaml.dump(spec_content),
            design_name=module_id,
            final_status="Success" if status == "Success" else "Fail",
            run_index=rtl_flow.kb.increment_run()
        )

        rtl_flow.kb.save()
    
    print(f"⏱️  Total Agentic flow time: {time.time() - initial_start_time:.2f} seconds")

if __name__ == "__main__":
    main()
