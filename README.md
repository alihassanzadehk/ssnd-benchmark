# Stochastic Scheduled Service Network Design (SSND) Benchmark Instances

This repository contains the benchmark instances and random demand scenarios used in the paper, forthcoming on Transportation Research Part B: Methodological:

> **Exact Solution Method for a Scheduled Service Network Design Problem**  
> Taherkhani, et al.  
> [SSRN link](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5127558)

---

## 📂 Contents
- **Split zip files** for the SSND instances:
  - `SSND_6node_split.zip.001`, `SSND_6node_split.zip.002`, ...
  - `SSND_7node_split.zip.001`, `SSND_7node_split.zip.002`, ...
- Python loader (`ssnd_loader.py`) to parse the text files inside the zips.

The dataset is split into multiple parts because GitHub has a 25 MB per-file limit.

---

## 🔄 How to Recombine and Extract

### **Option 1: Using 7-Zip (Windows)**
1. Download **all** parts (`.zip.001`, `.zip.002`, ...).
2. Place them in the same folder.
3. Right-click the first part (e.g., `SSND_6node_split.zip.001`) → **7-Zip → Extract here**.

### **Option 2: Using Command Line (Mac/Linux)**
```bash
# Combine parts
cat SSND_6node_split.zip.* > SSND_6node_full.zip
unzip SSND_6node_full.zip

cat SSND_7node_split.zip.* > SSND_7node_full.zip
unzip SSND_7node_full.zip
