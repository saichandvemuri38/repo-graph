# 🔗 Dependency Chain Graph - Visual Map

## Complete Call Graph Visualization

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                                                                        ┃
┃                    🎯 MASTER NODE - generate_report()                 ┃
┃                                                                        ┃
┃  Calls: preprocess_pipeline() + extract_features() +                 ┃
┃          analyze_distribution() + transform_data() + calculate_mean() ┃
┃                                                                        ┃
┗━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
         ║        ║        ║        ║
    ┌────┘        │        │        └──────┬─────────┐
    │             │        │               │         │
    ▼             ▼        ▼               ▼         ▼
┌──────────────┐ ┌──────────────────────┐ ┌─────────────┐ ┌──────────┐
│ preprocess   │ │ extract_features()   │ │   analyze   │ │transform │
│ _pipeline()  │ │  [KEY AGGREGATOR]    │ │_distribution│ │  _data() │
├──────────────┤ │                      │ │             │ └──────────┘
│ CALLS:       │ │ CALLS:               │ │ CALLS:      │       │
│ • validate   │ │ • preprocess_        │ │ • extract   │       │
│   _input()   │ │   pipeline()         │ │   _features │       │
│ • check      │ │ • calculate_mean()   │ │ • transform │       ├─────┐
│   _bounds()  │ │ • calculate_std_dev()│ │   _data()   │       │     │
│ • clean      │ │ • calculate_         │ │ • calculate │       │     │
│   _outliers()│ │   percentiles()      │ │   _mean()   │       ▼     ▼
└──┬───────────┘ └──────┬───────────────┘ │ • calculate │   ┌──────────────┐
   │                    │                 │   _std_dev()│   │apply_        │
   │                    │                 └─────────────┘   │normalization │
   │                    └─────────────────────┬──────────────┤()            │
   │                                          │              │             │
   │    ┌────────────────────────────────────┘   ┌──────────▶│ CALLS:      │
   │    │                                        │           │ • validate  │
   │    ▼                                        │           │ • normalize │
   └──►validate_input()          ┌───────────────┘           └──┬──────────┘
       ├──◀ Called by: 4+ nodes  │                             │
       │                         │                             │
       ├─────────────────────────┴─────────────────────────────┘
       │
       ▼
   check_bounds()
       ├──◀ Called by: preprocess_pipeline()
       │
       ▼
   clean_outliers()
       ├──◀ Called by: preprocess_pipeline()
       │
       └──► validate_input()  [nested call]
           
           ┌──────────────────────────────────────┐
           │ COMPUTE FUNCTIONS (Features Layer)   │
           └──────────────────────────────────────┘
                        ▼
                calculate_mean()
                ├──◀ Called by: 3+ nodes
                │   • calculate_std_dev()
                │   • extract_features()
                │   • analyze_distribution()
                │
                ▼
           calculate_std_dev()
           ├──◀ Called by: 2+ nodes
           │   • extract_features()
           │   • analyze_distribution()
           │
           ├──► calculate_mean()  [dependency]
           │
           ▼
       calculate_percentiles()
       └──◀ Called by: extract_features()

           ┌──────────────────────────────────────┐
           │ TRANSFORMATION PIPELINE              │
           └──────────────────────────────────────┘
                        ▼
            normalize_data()
            └──◀ Called by: apply_normalization()

                        ▼
            apply_scaling()
            ├──◀ Called by: transform_data()
            │
            └──► apply_normalization()  [dependency]
```

---

## Dependency Matrix (Who Calls Whom)

```
                 validate check clean preprocess extract calculate calculate calculate apply apply transform analyze generate
                 _input  _bounds _out  _pipeline _features_mean  _std_dev _percentiles_norm_scale _data _dist  _report
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
validate_input     -        -      ✓      ✓         -        -         -          -        ✓       -      -       -         -
check_bounds       -        -      -      ✓         -        -         -          -        -       -      -       -         -
clean_outliers     ✓        -      -      ✓         -        -         -          -        -       -      -       -         -
preprocess_pipe    -        -      -      -         ✓        -         -          -        -       -      -       -         ✓
extract_features   -        -      -      ✓         -        ✓         ✓          ✓        -       -      ✓       -         ✓
calculate_mean     -        -      -      -         ✓        -         ✓          -        -       -      -       ✓         ✓
calculate_std_dev  -        -      -      -         ✓        ✓         -          -        -       -      -       ✓         -
calculate_percent  -        -      -      -         ✓        -         -          -        -       -      -       -         -
apply_normaliz     ✓        -      -      -         -        -         -          -        -       ✓      ✓       -         -
apply_scaling      -        -      -      -         -        -         -          -        ✓       -      ✓       -         -
transform_data     ✓        -      -      -         -        -         -          -        ✓       ✓      -       -         ✓
analyze_distrib    -        -      -      -         ✓        ✓         ✓          -        -       -      ✓       -         ✓
generate_report    -        -      -      ✓         ✓        ✓         -          -        -       -      ✓       ✓         -
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IN-DEGREE (times   4        1      1      4         4        3         3          1        2       2      3       3         0
called by others)
```

---

## Call Paths (Execution Flows)

### Path 1: Complete Pipeline Execution
```
generate_report()
  ├─→ preprocess_pipeline()
  │    ├─→ validate_input()  ✓
  │    ├─→ check_bounds()    ✓
  │    └─→ clean_outliers()
  │         └─→ validate_input()  ✓
  │
  ├─→ extract_features()
  │    ├─→ preprocess_pipeline()  [REUSE - already executed]
  │    ├─→ calculate_mean()  ✓
  │    ├─→ calculate_std_dev()
  │    │    └─→ calculate_mean()  ✓
  │    └─→ calculate_percentiles()  ✓
  │
  ├─→ analyze_distribution()
  │    ├─→ extract_features()  [REUSE]
  │    ├─→ transform_data()
  │    │    ├─→ validate_input()  [REUSE]
  │    │    ├─→ apply_normalization()
  │    │    │    └─→ normalize_data()  ✓
  │    │    └─→ apply_scaling()
  │    │         └─→ apply_normalization()  [REUSE]
  │    ├─→ calculate_mean()  [REUSE]
  │    └─→ calculate_std_dev()  [REUSE]
  │
  ├─→ transform_data()  [REUSE]
  │
  └─→ calculate_mean()  [REUSE]
```

**Execution Statistics:**
- Total function calls: ~25-30 (with reuse)
- Unique functions executed: 17
- Reused nodes: 7 (validate_input, calculate_mean, preprocess_pipeline, extract_features, etc.)

---

## Blast Radius Analysis

### High-Impact Nodes (Changing = affects many)
```
🔴 CRITICAL: validate_input()
   └─ Impact if changed: 4+ functions break
   └─ Callers: clean_outliers, preprocess_pipeline, apply_normalization, transform_data
   └─ Risk Level: CRITICAL

🟠 HIGH: calculate_mean()
   └─ Impact if changed: 3+ functions break
   └─ Callers: calculate_std_dev, extract_features, analyze_distribution, generate_report
   └─ Risk Level: HIGH

🟠 HIGH: extract_features()
   └─ Impact if changed: 2+ functions break
   └─ Callers: analyze_distribution, generate_report
   └─ Risk Level: HIGH
```

### Safe-to-Change Nodes
```
🟢 LOW: normalize_data()
   └─ Called by: apply_normalization only
   └─ Impact if changed: 1 function
   └─ Risk Level: LOW

🟢 LOW: calculate_percentiles()
   └─ Called by: extract_features only
   └─ Impact if changed: 1 function
   └─ Risk Level: LOW
```

---

## Dependency Chains for Code Navigation

### For Understanding Data Flow:
```
Raw Data
    │
    ▼
validate_input() ──────────────┐
    │                          │
    ▼                          │
check_bounds()                 │
    │                          │
    ├──────────────────────────┤
    │                          │
    ▼                          │
clean_outliers() ◀─────────────┘
    │
    ▼
[Preprocessed Data]
    │
    ├─────────────────┬─────────────────┐
    │                 │                 │
    ▼                 ▼                 ▼
calculate_mean() calculate_std_dev() calculate_percentiles()
    │                 │
    └─────────┬───────┘
              │
              ▼
    [Features Extracted]
              │
              ├──────────────┬──────────────┐
              │              │              │
              ▼              ▼              ▼
         validate_input() normalize_data() [Features]
              │              │
              └──────┬───────┘
                     │
                     ▼
            [Transformed Data]
                     │
                     ├────────────────┬────────────────┐
                     │                │                │
                     ▼                ▼                ▼
              [Report Data]    [Analysis Data]  [Transform Data]
                     │                │                │
                     └────────┬───────┴────────┬───────┘
                              │                │
                              ▼                ▼
                      [Final Report]
```

---

## Layer Interaction Map

```
                         ┌─────────────────────────┐
                         │   generate_report()     │
                         │  [ORCHESTRATION]        │
                         └────────┬────────────────┘
                                  │
                  ┌───────────────┼───────────────┐
                  │               │               │
        ┌─────────▼────────┐ ┌───▼────────────┐  │
        │ extract_features │ │analyze_distrib │  │
        │ + transform_data │ │ + transform    │  │
        └────────┬─────────┘ └───┬────────────┘  │
                 │               │               │
        ┌────────▼───────────────▼────────┐      │
        │  Feature & Transform Pipeline   │      │
        │  • calculate_mean()             │      │
        │  • calculate_std_dev()          │      │
        │  • calculate_percentiles()      │      │
        │  • normalize_data()             │      │
        └────────┬──────────────┬─────────┘      │
                 │              │                │
        ┌────────▼──────────────▼────────┐      │
        │ Preprocessing Layer             │      │
        │ • validate_input()              │◀─────┘
        │ • check_bounds()                │
        │ • clean_outliers()              │
        └────────────────────────────────┘
```

---

## Usage Pattern for Dependency Graphs

This structure is perfect for:

1. **Data Pipeline Testing** - Test each layer independently
2. **Caching Strategy** - Reuse precomputed means & features
3. **Parallel Execution** - Independent branches can run in parallel
4. **Error Handling** - Fail fast at validation layer
5. **Performance Optimization** - Identify bottleneck nodes
6. **Documentation** - Clear layer separation for readers
