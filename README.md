# Autonomous Multi-Agent Flows for ASIC Design and Verification Using Large and Small Language Models

> **Diploma Thesis**  
> **University of Thessaly** | Department of Electrical and Computer Engineering | Volos, Greece 
> **Author:** Monastiriotis Theodoros  

---

This repository contains the source code, framework implementations, benchmarks, and documentation for my Diploma Thesis exploring the transition from agent-assisted EDA toward **Fully Autonomous Spec2Tapeout (Specification to Tapeout) ASIC design and verification**. 

The project introduces modular multi-agent architectures that leverage both Large Language Models (LLMs) and Small Language Models (SLMs) to orchestrate Electronic Design Automation (EDA) tools, handle Synopsys Design Constraints (SDC), generate RTL/Testbenches, and iteratively refine hardware designs via feedback-driven validation loops.

---

## 🏆 Benchmark Focus (ICLAD Hackathon 2025)

The core evaluation of this thesis focuses on solving two distinct benchmarks from the **ICLAD Hackathon 2025**, targeting both ASIC designing and verification:

* **ASU Spec2Tapeout Benchmark:** Focuses on complete ASIC design automation. It challenges the agentic flow to take YAML specifications and autonomously generate synthesizable RTL, constraint files (SDC), and a final tapeout-ready OpenROAD Database (ODB) file via an automated OpenROAD Flow Scripts (ORFS) pipeline.
* **Google Verification Benchmark:** Focuses on autonomous functional verification. It challenges the multi-agent system to read hardware design specifications and generate testbenches, aiming to achieve full functional closure.

---

## 📂 Repository Architecture & Contents

The repository is organized as follows:

### 🚀 Core Implementations & Benchmarks

*   **`ASU_Spec2Tapeout/`**  
    Contains the framework, multi-agent architecture, and design code implemented for the ASU Spec2Tapeout challenge. 
*   **`Google_Verification/`**  
    Contains the multi-agent testbench generation infrastructure, and design code implemented for Google's verification benchmark.

### 📄 Documentation & Deliverables

*   **`Thesis.pdf`**  
    The complete, formal text of the diploma thesis detailing the background, related work, methodology, architectural design, and extensive experimental results.
*   **`Thesis_Presentation.pptx`**  
    The slides used for the formal thesis presentation, summarizing the core contributions, LLM vs. SLM trade-offs, and key performance metrics.
*   **`requirements.txt`**  
    The Python dependencies and software package versions required to run the agentic environments and language model execution pipelines.
*   **`README.md`**  
    This file, providing an overview and navigation guide for the repository.

---

## 🛠️ Key Architectural Contributions

Based on the thesis methodology, this implementation focuses on:
1. **Modular Multi-Agent Systems:** Specialized execution and reasoning agents assigned to distinct ASIC phases (RTL, TB, SDC, and Physical Design automation).
2. **Hybrid LLM-SLM Frameworks:** Pairing the deep reasoning power of heavy LLMs with the local deployment, low latency, and IP-security advantages of fine-tuned/distilled SLMs.
3. **Feedback-Driven Optimization:** Closing the loop between generative AI and commercial EDA tools to parse error logs, fix syntax/timing violations, and converge with minimal engineering intervention.

---

## 🧑‍💻 Author

*   **Monastiriotis Theodoros**  
    Department of Electrical and Computer Engineering, University of Thessaly, Volos, Greece