# Google Verification Problem Statement

## 🎯 Goal

In this design verification challenge the objective is to build an agentic flow that writes correct simulation testbenches based on the natural language RTL specification.

The information available to the agent:
- Natural language specification of a Verilog module
- 31 implementations of the module (mutants) where only one is correct

---

## 📂 Directory Structure

```plaintext
Google_Verification/
|
├── README.md
|
├── LICENSE
|
├── example_problem/
|   ├── specification.md
│   └── solutions/
│       └── Required solution files of the enc_bin2gray design.
|
├── visible_problems/
│   ├── cdc_fifo_flops_push_credit
│   ├── counter
|       ├── mutant_i.v
|           └── *.v files. Input mutants for each problem.
|       ├── specification.md
|           └── Natural language specification for each problem.
|       └── tb.v
|           └── Empty testbench file, where the final generated testbench should be saved.
```

> 💡 **Note:** The `visible_problems` folder exists because the Hackathon evaluation flow tests `verification_agentic_flow.py` against hidden, unprovided problem sets during grading.

---

## 🛠️ Agentic Flow Usage

1. Install the provided requirements in a new Python environment: `pip install -r requirements.txt`
2. Set the `DESIGN_SUBFOLDER` global variable inside `verification_agentic_flow.py` to the design you want to verify.
3. Replace `your_gemini_api_key` in `verification_agentic_flow.py` with your actual Google Gemini API key.
4. Ensure your working directory is set to `visible_problems/` and that `mutant_evaluator.py` is present in that same folder.
5. Run the script: `python3 verification_agentic_flow.py`

> 💡 **Note:** `verification_agentic_flow.py` contains several global parameters at the top of the file to configure the execution flow.