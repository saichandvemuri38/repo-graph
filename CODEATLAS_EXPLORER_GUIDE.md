# 🌐 Interactive Graph Exploration Guide with CodeAtlas

## Overview

The complex dependency chain in `day-1.py` has been indexed by CodeAtlas with **17 symbols** and **45 edges**. This document shows how to explore it interactively.

---

## Quick Start

### 1. Start CodeAtlas Server

```bash
cd /Users/saichandvemuri/Downloads/Dsa/python
sh scripts/codeatlas.sh serve
```

**Expected Output:**
```
INFO: CodeAtlas Web Server
API listening on http://localhost:4848
```

### 2. Open in Browser

Navigate to: **http://localhost:4848**

You'll see:
- **Force-directed graph** (interactive, draggable nodes)
- **Search bar** to find functions
- **Details panel** with function information
- **Tabs** for Code, Flows, Clusters, and more

---

## Explorer Queries & Commands

### A. Finding the Master Node

**Search:** `generate_report`

**What you'll see:**
```
generate_report()
├─ Depends on (in-edges):
│   ├─ preprocess_pipeline()
│   ├─ extract_features()
│   ├─ analyze_distribution()
│   ├─ transform_data()
│   └─ calculate_mean()
└─ Called by: [none] (it's the master entry point)
```

**Quick Facts:**
- **In-degree:** 0 (nothing calls it; it's the top-level orchestrator)
- **Out-degree:** 5 (directly depends on 5 functions)
- **Transitive Dependencies:** 15+ (counting all nested calls)

---

### B. Exploring Reusable Utilities

**Search:** `validate_input`

**What you'll see:**
```
validate_input()
├─ Called by:
│   ├─ clean_outliers()
│   ├─ preprocess_pipeline()
│   ├─ apply_normalization()
│   └─ transform_data()
├─ Depends on: [none] (foundational utility)
└─ Execution Flows: 
    ├─ Flow 1: generate_report → ... → validate_input
    ├─ Flow 2: generate_report → transform_data → validate_input
    └─ [Multiple paths]
```

**Key Insight:** This is a **critical bottleneck**. Changes here affect 4+ functions.

---

### C. Tracing Feature Extraction

**Search:** `extract_features`

**What you'll see:**
```
extract_features()
├─ Depends on:
│   ├─ preprocess_pipeline()
│   ├─ calculate_mean()
│   ├─ calculate_std_dev()
│   └─ calculate_percentiles()
├─ Called by:
│   ├─ analyze_distribution()
│   └─ generate_report()
└─ Cluster: Feature Extraction Hub
```

**Interactive Options:**
1. Click on any dependent → see its code and downstream effects
2. Click "Flows" tab → see all execution paths through this function
3. Click "Impact" → see what breaks if you modify this function

---

### D. Understanding Statistical Calculations

**Search:** `calculate_mean`

**What you'll see:**
```
calculate_mean()
├─ Called by:
│   ├─ calculate_std_dev()      [nested: std_dev → mean]
│   ├─ extract_features()       [direct: extract → mean]
│   ├─ analyze_distribution()   [direct: analyze → mean]
│   └─ generate_report()        [direct: report → mean]
├─ Depends on: [none] (pure computation)
└─ Reuse Pattern: 3+ callers = HIGH REUSABILITY
```

**Quick Facts:**
- **Most reused function** in the feature layer
- **In-degree:** 3
- **Out-degree:** 0
- **Risk if modified:** Changes affect 3+ dependent functions

---

### E. Analyzing Transformation Pipeline

**Search:** `transform_data`

**What you'll see:**
```
transform_data()
├─ Depends on:
│   ├─ validate_input()
│   ├─ apply_normalization()
│   └─ apply_scaling()
├─ Called by:
│   ├─ analyze_distribution()
│   └─ generate_report()
└─ Complexity: HIGH (3+ direct dependencies + transitive)
```

**Network View:**
```
generate_report
    ↓
transform_data ←─ analyze_distribution
    ├── validate_input (REUSE)
    ├── apply_normalization
    │    └── normalize_data
    └── apply_scaling
         └── apply_normalization (NESTED REUSE)
```

---

## Advanced Exploration Techniques

### Technique 1: Finding Call Paths

**Question:** "How does execution flow from `generate_report` to `normalize_data`?"

**Steps:**
1. Search for `generate_report`
2. Click the "Flows" tab
3. Look for paths containing `normalize_data`
4. One path: `generate_report → transform_data → apply_normalization → normalize_data`

---

### Technique 2: Blast Radius Analysis

**Question:** "What breaks if I change `calculate_std_dev()`?"

**Steps:**
1. Search for `calculate_std_dev`
2. Look at "Called by" section
3. You'll see:
   ```
   Called by:
   ├─ extract_features() → Affects generate_report, analyze_distribution
   └─ analyze_distribution() → Affects generate_report
   ```
4. **Blast Radius:** 2 intermediate functions, 1 master function affected

---

### Technique 3: Identifying Aggregation Points

**Question:** "Which functions combine results from multiple sources?"

**Look for functions with HIGH IN-DEGREE:**
```
Ranked by in-degree (how many functions depend on them):

🔴 CRITICAL (4+ callers):
   • validate_input() — in-degree: 4

🟠 HIGH (3 callers):
   • calculate_mean() — in-degree: 3
   • extract_features() — in-degree: 3

🟡 MEDIUM (2 callers):
   • apply_normalization() — in-degree: 2
   • transform_data() — in-degree: 2

🟢 LOW (1 caller):
   • normalize_data() — in-degree: 1
   • calculate_percentiles() — in-degree: 1
```

---

### Technique 4: Finding Chains & Sub-Chains

**Search Pattern:** Chain detection

**Example Chain Found:**
```
Chain: calculate_std_dev → calculate_mean
       (std_dev depends on mean)

Chain: apply_scaling → apply_normalization → normalize_data
       (scaling depends on normalization depends on data normalization)
```

**Use Case:** Identifying code sequences that always run together.

---

## Graph Navigation Tips

### Using the Details Panel

**Left side = Upstream Dependencies:**
- Shows what this function calls
- Click to see implementation

**Right side = Downstream Dependants:**
- Shows what calls this function
- Click to see the calling code

**Tabs:**
- **Details:** Basic info (in/out degree, complexity)
- **Code:** Actual function code
- **Flows:** All execution paths through this function
- **Clusters:** Which logical group this function belongs to
- **Nexus AI:** AI-powered analysis (if enabled)

---

### Layout Options

Click the layout buttons to reorganize the graph:

1. **Force Layout** (default)
   - Nodes repel each other
   - Good for seeing overall structure
   - Reveals clusters naturally

2. **Tree Layout**
   - Hierarchical from top to bottom
   - Perfect for seeing dependency layers
   - Shows `generate_report` at top, foundation utilities at bottom

3. **Radial Layout**
   - Center node connects outward
   - Good for focusing on one function
   - Shows "rings" of dependencies

**Pro Tip:** Switch to **Tree Layout** to see the 5-layer pyramid structure!

---

## Key Insights to Explore

### 1. The 5-Layer Pyramid

**Use Tree Layout, then click on `generate_report`:**

```
                    generate_report [Layer 5]
                           ↑
                    [Layer 4 Hub]
                   /         |         \
            extract_     analyze_    transform_
            features     distrib     data
                ↑            ↑         ↑
            [Layer 3]    [Layer 3]  [Layer 2]
              |            |          |
            [Layer 2] ──→ preprocess_pipeline
              |
            [Layer 1] ──→ validate_input
```

---

### 2. Reusability Hotspots

**Functions called multiple times:**

| Function | Called By | Frequency |
|----------|-----------|-----------|
| `validate_input()` | 4 functions | ★★★★ |
| `calculate_mean()` | 3 functions | ★★★ |
| `preprocess_pipeline()` | 2 functions | ★★ |
| `extract_features()` | 2 functions | ★★ |
| `apply_normalization()` | 2 functions | ★★ |

---

### 3. Gateway Nodes

Functions that act as convergence points:

- **preprocess_pipeline()** → Gateway for all validation & cleaning
- **extract_features()** → Gateway for statistical features
- **transform_data()** → Gateway for data transformation
- **generate_report()** → Master gateway orchestrating all

---

## Queries You Can Run

### Via CodeAtlas Web UI

1. **Search for symbols:** `search("function_name")`
2. **Filter by type:** Functions, classes, modules
3. **Find dependencies:** Click any node to see in/out edges
4. **Trace paths:** Use "Flows" tab to see execution chains
5. **View code:** Click function name to see implementation

---

## Exporting Your Findings

### Generate Reports

**Option 1: HTML Report**
```bash
cd /Users/saichandvemuri/Downloads/Dsa/python
sh scripts/codeatlas.sh report --output=my_analysis.html
```

**Option 2: Visual Export**
- Screenshot the graph in your desired layout
- Right-click node → "Copy" to get information
- Share with team

---

## Common Questions & How to Find Answers

| Question | Steps | Result |
|----------|-------|--------|
| "What's the complete dependency chain?" | Search `generate_report` → Flows tab | Full execution tree |
| "Is it safe to change `validate_input()`?" | Search `validate_input()` → See "Called by" section | Shows 4+ dependants = RISKY |
| "How many layers deep?" | Switch to Tree Layout | Shows 5 layers |
| "Which functions are independent?" | Look for 0 in-degree | None (all part of chain) |
| "What's the most critical node?" | Find highest in-degree | `validate_input()` |
| "Can I run features without cleaning?" | Trace `extract_features()` | Must run through `preprocess_pipeline()` |

---

## Tips for Deep Exploration

1. **Start with `generate_report()`** — it's the entry point
2. **Use Tree Layout** — see the 5-layer structure clearly
3. **Track reusable functions** — they appear multiple times
4. **Check in-degree counts** — higher = more critical
5. **Read the code** — CodeAtlas shows implementation for each function
6. **Trace specific paths** — use Flows tab to follow execution

---

## Integration with Your Code

The graph is **live** — changes to `day-1.py` will be reflected:

```bash
# Edit day-1.py
# The graph auto-updates
# Refresh browser to see changes
```

This makes CodeAtlas useful for:
- Refactoring safely (see blast radius before editing)
- Adding new functions (understand where they fit in layers)
- Debugging (trace through execution flows)
- Documentation (visual dependency map)

---

## Keyboard Shortcuts (on graph page)

- **Click & Drag:** Move nodes around
- **Scroll:** Zoom in/out
- **Double-click node:** Focus on that node
- **Click node:** Show details panel
- **Search box:** Find functions by name

---

Enjoy exploring the complex dependency chain! 🚀
