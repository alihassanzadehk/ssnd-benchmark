from __future__ import annotations
import re, ast, io, zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Iterable, Any, Optional

# -----------------------------
# Data containers
# -----------------------------

TNode = Tuple[int, int]                       # (node, t)
Arc   = Tuple[TNode, TNode]                   # ((i,t1),(j,t2))

@dataclass
class SSNDInstance:
    name: str
    NodeSize: int
    TimePeriods: List[int]
    RequestSize: int
    ServiceNoPerArc: int
    ServiceCapacity: int
    FastServiceRatio: float
    RevenueRange: Tuple[Tuple[int,int], Tuple[int,int]]
    ReqDemandRange: Tuple[int,int]
    ServiceCost: int
    TransCost: int
    HoldingCost: int

    # Topology
    physical_arcs: List[Tuple[int,int]] = field(default_factory=list)
    tnodes: List[TNode] = field(default_factory=list)  # can be derived if needed

    # Services & costs
    services: List[Arc] = field(default_factory=list)  # arcsE
    cs: Dict[Arc, float] = field(default_factory=dict)
    fs: Dict[Arc, float] = field(default_factory=dict)
    us: Dict[Arc, float] = field(default_factory=dict)  # uniform capacity applied

    # Requests
    reqs: List[int] = field(default_factory=list)
    os: Dict[int, int] = field(default_factory=dict)
    ds: Dict[int, int] = field(default_factory=dict)
    alphas: Dict[int, int] = field(default_factory=dict)
    betas: Dict[int, int] = field(default_factory=dict)
    is_contract: Dict[int, bool] = field(default_factory=dict)  # True if contract-based
    rhos: Dict[int, float] = field(default_factory=dict)        # unit revenue
    ws: Dict[int, int] = field(default_factory=dict)            # baseline demand (mu)

    # Holding arcs & cost
    holding_arcs: List[Arc] = field(default_factory=list)       # arcsH
    chs: Dict[Arc, float] = field(default_factory=dict)

    # Penalties
    alphaPsis: Dict[Tuple[int,int], float] = field(default_factory=dict)  # (k,t) -> penalty
    betaPsis: Dict[Tuple[int,int], float]  = field(default_factory=dict)

    # Exec arcs in/out per time node
    arcsEin: Dict[TNode, List[Arc]] = field(default_factory=dict)
    arcsEout: Dict[TNode, List[Arc]] = field(default_factory=dict)


@dataclass
class WScenarioSet:
    node_size: int
    Kmax: int
    freq: int
    serv_cap: int
    nu: float
    # baseline means per request
    w_mu: Dict[int, int]
    # full scenario draws per request (list of ints)
    rnd_ws: Dict[int, List[int]]


# -----------------------------
# Helpers
# -----------------------------

def _lit(x: str):
    """safe literal eval with nicer errors."""
    try:
        return ast.literal_eval(x)
    except Exception as e:
        raise ValueError(f"literal_eval failed on: {x[:200]}...") from e

def _clean(s: str) -> str:
    return s.strip().replace("\r", "")

def _section_blocks(text: str) -> Dict[str, List[str]]:
    """
    Split the file into named sections based on known headers.
    Returns a dict: section_name -> list of lines (without the header line).
    """
    lines = [_clean(l) for l in text.splitlines()]
    blocks: Dict[str, List[str]] = {}

    def take_table(start_idx: int) -> Tuple[List[str], int]:
        """Collect lines until a blank line or EOF."""
        out = []
        i = start_idx
        while i < len(lines) and lines[i] != "":
            out.append(lines[i])
            i += 1
        return out, i

    # First: parse header key: value lines until we hit "Arcs "
    i = 0
    header = {}
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("Arcs "):
            break
        if ln == "":
            i += 1
            continue
        if " " in ln:
            key, val = ln.split(" ", 1)
            header[key] = val
        i += 1
    blocks["HEADER"] = [f"{k} {v}" for k, v in header.items()]

    # Physical arcs
    if i < len(lines) and lines[i].startswith("Arcs "):
        blocks["ARCS"] = [lines[i][len("Arcs "):]]
        i += 1

    # Now scan through known tables in order
    tables = [
        ("SERVICES", "serviceID\tServices\torigin\talpha\tdestination\tbeta\tTranCost\tfs"),
        ("REQS", "reqs\torigins\tdestinations\talphas\tbetas\tcontract_based\trhos\tws"),
        ("HOLDING", "HoldingArcs\tHoldingCost"),
        ("PSI", "reqs\ttimes\talphaPsi\tbetaPsi"),
        ("EIN", "TimeNodes\tExecArcsIn"),
        ("EOUT", "TimeNodes\tExecArcsOut"),
    ]
    for sec_name, header_row in tables:
        # skip blank lines
        while i < len(lines) and lines[i] == "":
            i += 1
        if i < len(lines) and lines[i] == header_row:
            i += 1
            tab, i = take_table(i)
            blocks[sec_name] = tab
        # else: section missing (okay)

    return blocks


def _parse_header(header_lines: List[str]) -> Dict[str, Any]:
    out = {}
    for ln in header_lines:
        key, val = ln.split(" ", 1)
        val = val.strip()
        if key in ("NodeSize", "RequestSize", "ServiceNoPerArc", "ServiceCapacity", "ServiceCost"):
            out[key] = int(val)
        elif key == "FastServiceRatio":
            out[key] = float(val)
        elif key == "TimePeriods":
            out[key] = _lit(val)  # list of ints
        elif key == "RevenueRange":
            out[key] = _lit(val)  # ((a,b),(c,d))
        elif key == "ReqDemandRange":
            out[key] = _lit(val)  # (LB,UB)
        elif key == "Trans/HoldingCost":
            # format: (2,1)
            th = _lit(val)
            out["TransCost"] = th[0]
            out["HoldingCost"] = th[1]
        elif key == "Name":
            out["Name"] = val
        else:
            # ignore anything unexpected; keep raw
            out[key] = val
    return out


def _parse_services(lines: List[str]) -> Tuple[List[Arc], Dict[Arc,float], Dict[Arc,float]]:
    services: List[Arc] = []
    cs: Dict[Arc, float] = {}
    fs: Dict[Arc, float] = {}
    for ln in lines:
        # serviceID \t ((i,t1),(j,t2)) \t i \t t1 \t j \t t2 \t TranCost \t fs
        parts = ln.split("\t")
        sid = int(parts[0])
        arc = _lit(parts[1])  # ((i,t1),(j,t2))
        c   = float(parts[6])
        f   = float(parts[7])
        services.append(arc)
        cs[arc] = c
        fs[arc] = f
    return services, cs, fs


def _parse_reqs(lines: List[str]):
    reqs = []
    os, ds, alphas, betas = {}, {}, {}, {}
    is_contract, rhos, ws = {}, {}, {}
    for ln in lines:
        parts = ln.split("\t")
        k = int(parts[0])
        reqs.append(k)
        os[k] = int(parts[1]); ds[k] = int(parts[2])
        alphas[k] = int(parts[3]); betas[k] = int(parts[4])
        is_contract[k] = (parts[5].strip().lower() == "true")
        rhos[k] = float(parts[6]); ws[k] = int(parts[7])
    return reqs, os, ds, alphas, betas, is_contract, rhos, ws


def _parse_holding(lines: List[str]):
    arcsH: List[Arc] = []
    chs: Dict[Arc, float] = {}
    for ln in lines:
        a_str, cost_str = ln.split("\t")
        a = _lit(a_str)
        c = float(cost_str)
        arcsH.append(a)
        chs[a] = c
    return arcsH, chs


def _parse_psis(lines: List[str]):
    alphaPsis, betaPsis = {}, {}
    for ln in lines:
        k_str, t_str, apsi_str, bpsi_str = ln.split("\t")
        k = int(k_str); t = int(t_str)
        alphaPsis[(k,t)] = float(apsi_str)
        betaPsis[(k,t)]  = float(bpsi_str)
    return alphaPsis, betaPsis


def _parse_exec_lists(lines: List[str]) -> Dict[TNode, List[Arc]]:
    out: Dict[TNode, List[Arc]] = {}
    for ln in lines:
        tn_str, arcs_str = ln.split("\t")
        tn = _lit(tn_str)                       # (n,t)
        arcs = _lit(arcs_str) if arcs_str else []  # list of arcs
        out[tn] = arcs
    return out


# -----------------------------
# Public API
# -----------------------------

_INSTANCE_RE = re.compile(r"ins_N(?P<N>\d+)_K(?P<K>\d+)_Freq(?P<F>\d+)_sCap(?P<C>\d+)\.txt$")
_W_RE        = re.compile(r"wScenarios_N(?P<N>\d+)_K(?P<K>\d+)_Freq(?P<F>\d+)_sCap(?P<C>\d+)_nu(?P<nu>[\d\.]+)\.txt$")

def load_instances_zip(zip_path: str) -> Dict[Tuple[int,int,int,int], SSNDInstance]:
    """
    Reads all 'ins_N{N}_K{K}_Freq{F}_sCap{C}.txt' files from a zip
    and returns a dict keyed by (N,K,F,C) -> SSNDInstance.
    """
    out: Dict[Tuple[int,int,int,int], SSNDInstance] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            m = _INSTANCE_RE.search(name.split("/")[-1])
            if not m:
                continue
            N, K, F, C = map(int, (m["N"], m["K"], m["F"], m["C"]))
            with zf.open(name) as fh:
                text = fh.read().decode("utf-8", errors="replace")
            blocks = _section_blocks(text)
            header = _parse_header(blocks.get("HEADER", []))
            arcs = _lit(blocks["ARCS"][0]) if "ARCS" in blocks else []

            services, cs, fs = _parse_services(blocks.get("SERVICES", []))
            reqs, os, ds, alphas, betas, is_contract, rhos, ws = _parse_reqs(blocks.get("REQS", []))
            arcsH, chs = _parse_holding(blocks.get("HOLDING", [])) if "HOLDING" in blocks else ([], {})
            alphaPsis, betaPsis = _parse_psis(blocks.get("PSI", [])) if "PSI" in blocks else ({}, {})
            arcsEin = _parse_exec_lists(blocks.get("EIN", [])) if "EIN" in blocks else {}
            arcsEout = _parse_exec_lists(blocks.get("EOUT", [])) if "EOUT" in blocks else {}

            inst = SSNDInstance(
                name=header.get("Name", f"ins_N{N}_K{K}_Freq{F}_sCap{C}"),
                NodeSize=int(header["NodeSize"]),
                TimePeriods=list(header["TimePeriods"]),
                RequestSize=int(header["RequestSize"]),
                ServiceNoPerArc=int(header["ServiceNoPerArc"]),
                ServiceCapacity=int(header["ServiceCapacity"]),
                FastServiceRatio=float(header["FastServiceRatio"]),
                RevenueRange=tuple(header["RevenueRange"]),
                ReqDemandRange=tuple(header["ReqDemandRange"]),
                ServiceCost=int(header["ServiceCost"]),
                TransCost=int(header["TransCost"]),
                HoldingCost=int(header["HoldingCost"]),
                physical_arcs=list(arcs),
                services=services,
                cs=cs,
                fs=fs,
                us={a: float(header["ServiceCapacity"]) for a in services},  # uniform capacity as generated
                reqs=reqs, os=os, ds=ds,
                alphas=alphas, betas=betas,
                is_contract=is_contract, rhos=rhos, ws=ws,
                holding_arcs=arcsH, chs=chs,
                alphaPsis=alphaPsis, betaPsis=betaPsis,
                arcsEin=arcsEin, arcsEout=arcsEout,
            )
            out[(N, K, F, C)] = inst
    return out


def load_w_scenarios_zip(zip_path: str) -> Dict[Tuple[int,int,int,int,float], WScenarioSet]:
    """
    Reads all 'wScenarios_N{N}_K{K}_Freq{F}_sCap{C}_nu{nu}.txt' files from a zip
    and returns a dict keyed by (N,K,F,C,nu) -> WScenarioSet.
    Each file contains rows: reqs \t ws \t rnd_ws (semicolon-separated).
    """
    out: Dict[Tuple[int,int,int,int,float], WScenarioSet] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            m = _W_RE.search(name.split("/")[-1])
            if not m:
                continue
            N, K, F, C = map(int, (m["N"], m["K"], m["F"], m["C"]))
            nu = float(m["nu"])
            with zf.open(name) as fh:
                text = fh.read().decode("utf-8", errors="replace")
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            # skip header
            if lines and lines[0].startswith("reqs"):
                lines = lines[1:]

            w_mu: Dict[int,int] = {}
            rnd_ws: Dict[int, List[int]] = {}
            for ln in lines:
                req_str, mu_str, draws_str = ln.split("\t")
                k = int(req_str)
                w_mu[k] = int(mu_str)
                rnd_ws[k] = [int(x) for x in draws_str.split(";") if x != ""]
            out[(N, K, F, C, nu)] = WScenarioSet(
                node_size=N, Kmax=K, freq=F, serv_cap=C, nu=nu,
                w_mu=w_mu, rnd_ws=rnd_ws
            )
    return out


# -----------------------------
# Example usage
# -----------------------------
if __name__ == "__main__":
    zip_path = "SSND Instances.zip"  # <- path to your zip

    instances = load_instances_zip(zip_path)
    print(f"Loaded {len(instances)} instances.")
    # Pick one
    (N,K,F,C), inst = next(iter(instances.items()))
    print("Example instance:", inst.name, "| services:", len(inst.services), "| reqs:", len(inst.reqs))

    wsets = load_w_scenarios_zip(zip_path)
    print(f"Loaded {len(wsets)} w-scenario sets.")
    # Example: get 10th random draw for request 1 (if present)
    key = next(iter(wsets.keys()))
    wset = wsets[key]
    rq = next(iter(wset.rnd_ws.keys()))
    if len(wset.rnd_ws[rq]) > 10:
        print(f"nu={wset.nu} | req {rq} | draw10:", wset.rnd_ws[rq][10])
