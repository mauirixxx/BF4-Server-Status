from __future__ import annotations

import getpass
import json
import shlex
import socket
import subprocess
import hashlib
import os
import signal
import sys
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/opt/bf4-ha/config/cluster.json")

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=yes",
]


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing configuration file: {path}")
    return json.loads(path.read_text())


def run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def ssh(ip: str, command: str, timeout: int = 15) -> tuple[int, str, str]:
    return run(["ssh", *SSH_OPTS, f"root@{ip}", command], timeout=timeout)



def run_input(cmd: list[str], data: str, timeout: int = 15) -> tuple[int, str, str]:
    """Run a command with UTF-8 stdin; used for journal transport without shell quoting."""
    try:
        p = subprocess.run(
            cmd, input=data, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def ssh_input(ip: str, command: str, data: str, timeout: int = 15) -> tuple[int, str, str]:
    return run_input(["ssh", *SSH_OPTS, f"root@{ip}", command], data, timeout=timeout)


def psql(ip: str, sql: str, timeout: int = 15) -> tuple[int, str, str]:
    command = (
        "sudo -u postgres "
        "psql -X -d postgres -At -F '|' "
        "-v ON_ERROR_STOP=1 "
        f"-c {shlex.quote(sql)}"
    )
    return ssh(ip, command, timeout=timeout)


def dig(server: str, name: str, rtype: str = "A") -> tuple[int, str, str]:
    return run(
        [
            "dig", "+time=2", "+tries=1",
            f"@{server}", name, rtype, "+short",
        ],
        timeout=5,
    )


def tcp_check(ip: str, port: int = 5432, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def banner(text: str) -> None:
    print()
    print("=" * 68)
    print(f" {text}")
    print("=" * 68)


def parse_pipe_rows(out: str) -> list[list[str]]:
    return [line.split("|") for line in out.splitlines() if line.strip()]


def discover_roles(cfg: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    states: dict[str, dict[str, Any]] = {}
    problems: list[str] = []

    for name, meta in cfg["nodes"].items():
        ip = meta["ip"]
        rc, host, err = ssh(ip, "hostname")
        if rc != 0:
            states[name] = {"reachable": False}
            problems.append(f"{name}: SSH unavailable ({err or 'unknown error'})")
            continue

        sql = """
SELECT pg_is_in_recovery();
SHOW wal_log_hints;
"""
        rc, out, err = psql(ip, sql)
        if rc != 0:
            states[name] = {"reachable": True, "postgresql": False}
            problems.append(f"{name}: PostgreSQL query failed ({err or out})")
            continue

        lines = [x.strip() for x in out.splitlines() if x.strip()]
        if len(lines) < 2:
            states[name] = {"reachable": True, "postgresql": False}
            problems.append(f"{name}: unexpected PostgreSQL output")
            continue

        role = "STANDBY" if lines[0] == "t" else "PRIMARY"
        states[name] = {
            "reachable": True,
            "postgresql": True,
            "role": role,
            "wal_log_hints": lines[1],
            "hostname": host.strip(),
        }

    return states, problems


def current_primary(states: dict[str, dict[str, Any]]) -> str | None:
    primaries = [name for name, s in states.items() if s.get("role") == "PRIMARY"]
    return primaries[0] if len(primaries) == 1 else None


OPERATOR_INPUT_TIMEOUT_SECONDS = 120


class OperatorInputTimeout(RuntimeError):
    pass


def _bounded_terminal_read(reader, prompt: str, timeout: int = OPERATOR_INPUT_TIMEOUT_SECONDS) -> str:
    if timeout <= 0:
        return reader(prompt)
    if not hasattr(signal, "SIGALRM"):
        return reader(prompt)
    previous = signal.getsignal(signal.SIGALRM)
    def _alarm(_signum, _frame):
        raise OperatorInputTimeout(f"operator input timed out after {timeout}s")
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return reader(prompt)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def prompt_secret(prompt: str, timeout: int = OPERATOR_INPUT_TIMEOUT_SECONDS) -> str:
    try:
        return _bounded_terminal_read(getpass.getpass, prompt, timeout)
    except (OperatorInputTimeout, EOFError, KeyboardInterrupt) as exc:
        print(f"ABORTED: {exc or 'operator input interrupted'}", file=sys.stderr)
        raise SystemExit(20)


def confirm_exact(required: str, prompt: str | None = None, timeout: int = OPERATOR_INPUT_TIMEOUT_SECONDS) -> bool:
    text = prompt or f"Type confirmation exactly ({required}): "
    try:
        return _bounded_terminal_read(input, text, timeout).strip() == required
    except (OperatorInputTimeout, EOFError, KeyboardInterrupt) as exc:
        print(f"ABORTED: {exc or 'operator input interrupted'}", file=sys.stderr)
        return False


def prompt_vcenter_password(username: str) -> str:
    return prompt_secret(f"vCenter password for {username}: ")


def vcenter_validate_target(
    cfg: dict[str, Any],
    node_name: str,
    password: str,
) -> tuple[bool, str]:
    try:
        import ssl
        from pyVim.connect import SmartConnect, Disconnect
        from pyVmomi import vim
    except Exception as exc:
        return False, f"pyVmomi unavailable: {exc}"

    vc = cfg["vcenter"]
    node = cfg["nodes"][node_name]
    context = ssl._create_unverified_context()

    try:
        si = SmartConnect(
            host=vc["server"],
            user=vc["username"],
            pwd=password,
            sslContext=context,
        )
    except Exception as exc:
        return False, f"vCenter login failed: {type(exc).__name__}: {exc}"

    try:
        content = si.RetrieveContent()
        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        try:
            matches = [vm for vm in view.view if vm.name == node["vm_name"]]
        finally:
            view.Destroy()

        if len(matches) != 1:
            return False, (
                f"expected exactly one VM named {node['vm_name']!r}; "
                f"found {len(matches)}"
            )

        vm = matches[0]
        if vm.runtime.host is None:
            return False, "target VM has no runtime ESXi host"

        actual_esxi = vm.runtime.host.name
        if actual_esxi != node["esxi"]:
            return False, (
                f"ESXi mismatch: expected {node['esxi']}, got {actual_esxi}"
            )

        session = content.sessionManager.currentSession
        if session is None:
            return False, "no authenticated vCenter session"

        priv = content.authorizationManager.HasPrivilegeOnEntity(
            entity=vm,
            sessionId=session.key,
            privId=["System.View", "VirtualMachine.Interact.PowerOff"],
        )

        if not bool(priv[0]):
            return False, "missing System.View"
        if not bool(priv[1]):
            return False, "missing VirtualMachine.Interact.PowerOff"

        return True, (
            f"{node['vm_name']} | ESXi={actual_esxi} | "
            f"power={vm.runtime.powerState} | PowerOff=True"
        )
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass


def utc_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def log_path(action: str) -> Path:
    p = Path("/opt/bf4-ha/logs")
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{action}-{utc_stamp()}.log"


class Tee:
    def __init__(self, path: Path):
        self.path = path
        self.fp = path.open("a", encoding="utf-8")

    def write(self, text: str) -> None:
        print(text, end="")
        self.fp.write(text)
        self.fp.flush()

    def line(self, text: str = "") -> None:
        self.write(text + "\n")

    def close(self) -> None:
        self.fp.close()


def vcenter_get_vm(cfg: dict[str, Any], node_name: str, password: str):
    import ssl
    from pyVim.connect import SmartConnect
    from pyVmomi import vim

    vc = cfg["vcenter"]
    node = cfg["nodes"][node_name]
    context = ssl._create_unverified_context()

    si = SmartConnect(
        host=vc["server"],
        user=vc["username"],
        pwd=password,
        sslContext=context,
    )

    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True
    )
    try:
        matches = [vm for vm in view.view if vm.name == node["vm_name"]]
    finally:
        view.Destroy()

    if len(matches) != 1:
        try:
            from pyVim.connect import Disconnect
            Disconnect(si)
        except Exception:
            pass
        raise RuntimeError(
            f"expected exactly one VM named {node['vm_name']!r}; "
            f"found {len(matches)}"
        )

    return si, content, matches[0]


def vcenter_target_info(
    cfg: dict[str, Any],
    node_name: str,
    password: str,
) -> tuple[bool, dict[str, Any] | str]:
    try:
        from pyVim.connect import Disconnect
        si, content, vm = vcenter_get_vm(cfg, node_name, password)
    except Exception as exc:
        return False, f"vCenter lookup failed: {type(exc).__name__}: {exc}"

    try:
        node = cfg["nodes"][node_name]
        if vm.runtime.host is None:
            return False, "target VM has no runtime ESXi host"

        actual_esxi = vm.runtime.host.name
        power_state = str(vm.runtime.powerState)

        session = content.sessionManager.currentSession
        if session is None:
            return False, "no authenticated vCenter session"

        priv = content.authorizationManager.HasPrivilegeOnEntity(
            entity=vm,
            sessionId=session.key,
            privId=["System.View", "VirtualMachine.Interact.PowerOff"],
        )

        info = {
            "vm_name": vm.name,
            "expected_esxi": node["esxi"],
            "actual_esxi": actual_esxi,
            "power_state": power_state,
            "system_view": bool(priv[0]),
            "poweroff": bool(priv[1]),
        }

        if vm.name != node["vm_name"]:
            return False, f"VM name mismatch: expected {node['vm_name']}, got {vm.name}"
        if actual_esxi != node["esxi"]:
            return False, (
                f"ESXi mismatch: expected {node['esxi']}, got {actual_esxi}"
            )
        if not info["system_view"]:
            return False, "missing System.View"
        if not info["poweroff"]:
            return False, "missing VirtualMachine.Interact.PowerOff"

        return True, info
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass


def vcenter_poweroff_and_verify(
    cfg: dict[str, Any],
    node_name: str,
    password: str,
    timeout: int = 120,
) -> tuple[bool, str]:
    import time
    from pyVim.connect import Disconnect
    from pyVmomi import vim

    try:
        si, content, vm = vcenter_get_vm(cfg, node_name, password)
    except Exception as exc:
        return False, f"vCenter lookup failed: {type(exc).__name__}: {exc}"

    try:
        node = cfg["nodes"][node_name]

        if vm.runtime.host is None:
            return False, "target VM has no runtime ESXi host"
        if vm.runtime.host.name != node["esxi"]:
            return False, (
                f"ESXi mismatch: expected {node['esxi']}, got {vm.runtime.host.name}"
            )

        power_state = str(vm.runtime.powerState)
        if power_state != "poweredOn":
            return False, (
                f"target must be poweredOn before live fencing; "
                f"current state is {power_state}"
            )

        session = content.sessionManager.currentSession
        if session is None:
            return False, "no authenticated vCenter session"

        priv = content.authorizationManager.HasPrivilegeOnEntity(
            entity=vm,
            sessionId=session.key,
            privId=["System.View", "VirtualMachine.Interact.PowerOff"],
        )
        if not bool(priv[0]) or not bool(priv[1]):
            return False, "required vCenter privileges are missing"

        try:
            task = vm.PowerOffVM_Task()
        except Exception as exc:
            return False, (
                f"could not submit PowerOffVM_Task: "
                f"{type(exc).__name__}: {exc}"
            )

        deadline = time.time() + timeout
        while time.time() < deadline:
            state = task.info.state
            if state == vim.TaskInfo.State.success:
                break
            if state == vim.TaskInfo.State.error:
                return False, f"vCenter PowerOff task failed: {task.info.error}"
            time.sleep(1)
        else:
            return False, "timed out waiting for vCenter PowerOff task"

        # Poll the VM's actual runtime power state independently of task success.
        deadline = time.time() + 30
        while time.time() < deadline:
            if str(vm.runtime.powerState) == "poweredOff":
                return True, "poweredOff"
            time.sleep(1)

        return False, (
            "PowerOff task succeeded but VM did not reach poweredOff state"
        )
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass


def controller_identity() -> str:
    """Return the short hostname of the controller executing this command."""
    return socket.gethostname().split(".", 1)[0]


def new_operation_id(now=None) -> str:
    """Create a human-readable unique HA transaction identifier."""
    from datetime import datetime, timezone
    import secrets
    if now is None:
        now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


def journal_initialize(state: dict[str, Any], *, phase: str = "fence-prepared") -> dict[str, Any]:
    """Add v3 controller ownership metadata to a newly-created HA journal."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    ctl = controller_identity()
    state["schema_version"] = 3
    state["operation_id"] = new_operation_id()
    state["revision"] = 1
    state["origin_controller"] = ctl
    state["current_controller"] = ctl
    state["takeover_count"] = 0
    state["created_at_utc"] = now
    state["updated_at_utc"] = now
    state["journal_phase"] = phase
    return state


JOURNAL_PHASE_FLAGS = {
    "fence-prepared": 0,
    "fence-guard-established": 0,
    "fence-completed": 1,
    "fence-completed-recovered": 1,
    "promotion-completed": 2,
    "reparent-completed": 3,
    "dns-completed": 4,
    "postcheck-completed": 5,
    "rejoin-guard-established": 5,
    "rejoin-reseed-prepared": 5,
    "rejoin-basebackup-completed": 5,
    "rejoin-completed": 6,
}
JOURNAL_FLAGS = ("fence_completed", "promotion_completed", "reparent_completed", "dns_completed", "postcheck_completed", "rejoin_completed")


def validate_journal_semantics(state: dict[str, Any]) -> None:
    if int(state.get("schema_version", 0)) < 3:
        return
    op = state.get("operation_id")
    if not isinstance(op, str) or not op or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in op):
        raise ValueError("invalid operation_id")
    owner = state.get("current_controller")
    if not isinstance(owner, str) or not owner:
        raise ValueError("missing current_controller")
    try:
        rev = int(state.get("revision", 0))
    except Exception as exc:
        raise ValueError("invalid revision") from exc
    if rev < 1:
        raise ValueError("revision must be >= 1")
    phase = state.get("journal_phase")
    if phase not in JOURNAL_PHASE_FLAGS:
        raise ValueError(f"unknown journal_phase {phase!r}")
    flags = [bool(state.get(k, False)) for k in JOURNAL_FLAGS]
    seen_false = False
    for flag in flags:
        if seen_false and flag:
            raise ValueError("journal completion flags are not a monotonic prefix")
        if not flag:
            seen_false = True
    expected = JOURNAL_PHASE_FLAGS[phase]
    if sum(flags) != expected:
        raise ValueError(f"journal phase {phase!r} requires exactly {expected} completed phase flags; got {sum(flags)}")
    if phase == "fence-guard-established" and not state.get("old_primary_postgresql_boot_guard"):
        raise ValueError("fence-guard-established requires boot-guard evidence")
    if phase in ("rejoin-reseed-prepared", "rejoin-basebackup-completed"):
        pgdata = state.get("rejoin_pgdata")
        backup = state.get("rejoin_old_pgdata_backup")
        if not isinstance(pgdata, str) or not pgdata.startswith("/var/lib/postgresql/"):
            raise ValueError(f"{phase} requires canonical rejoin_pgdata")
        if not isinstance(backup, str) or not backup.startswith(pgdata + ".pre-rejoin-"):
            raise ValueError(f"{phase} requires original PGDATA backup identity")

    if phase == "rejoin-basebackup-completed":
        if state.get("rejoin_basebackup_completed") is not True:
            raise ValueError("rejoin-basebackup-completed requires completion evidence")
        if state.get("rejoin_basebackup_pgdata") != state.get("rejoin_pgdata"):
            raise ValueError("rejoin-basebackup-completed requires matching PGDATA identity")
        if not isinstance(state.get("rejoin_basebackup_source_primary"), str) or not state.get("rejoin_basebackup_source_primary"):
            raise ValueError("rejoin-basebackup-completed requires source-primary identity")
        if not isinstance(state.get("rejoin_basebackup_slot"), str) or not state.get("rejoin_basebackup_slot"):
            raise ValueError("rejoin-basebackup-completed requires replication-slot identity")


def journal_assert_owned(state: dict[str, Any]) -> None:
    if int(state.get("schema_version", 0)) < 3:
        return
    validate_journal_semantics(state)
    owner = state.get("current_controller")
    here = controller_identity()
    if owner != here:
        raise RuntimeError(
            f"HA operation {state.get('operation_id','?')} is owned by {owner}; "
            f"this controller is {here}. Run bf4-ha-takeover explicitly before continuing."
        )


def journal_advance(state: dict[str, Any], phase: str) -> dict[str, Any]:
    from datetime import datetime, timezone

    # Callers set the completion flag/evidence for the TARGET phase before
    # advancing.  Validating the state against the OLD phase here therefore
    # rejects a legitimate transition (for example fence_completed=True while
    # journal_phase is still fence-guard-established).  Ownership must be
    # checked independently, then the phase transition is made atomically in
    # memory and the resulting target state is semantically validated before
    # any journal commit can persist it.
    if int(state.get("schema_version", 0)) >= 3:
        owner = state.get("current_controller")
        here = controller_identity()
        if owner != here:
            raise RuntimeError(
                f"HA operation {state.get('operation_id','?')} is owned by {owner}; "
                f"this controller is {here}. Run bf4-ha-takeover explicitly before continuing."
            )
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    state["journal_phase"] = phase
    validate_journal_semantics(state)
    return state


def journal_recovery_path(cfg: dict[str, Any], operation_id: str) -> Path:
    state_dir = Path(cfg["state"].get("dir", Path(cfg["state"]["fence_file"]).parent))
    return state_dir / "recovery" / f"{operation_id}.json"


def journal_payload(state: dict[str, Any]) -> str:
    return json.dumps(state, indent=2, sort_keys=True) + "\n"


def journal_sha256(state: dict[str, Any]) -> str:
    return hashlib.sha256(journal_payload(state).encode("utf-8")).hexdigest()


def journal_effective_for_operation(cfg: dict[str, Any], operation_id: str) -> tuple[Path, dict[str, Any]] | None:
    active = Path(cfg["state"]["fence_file"])
    recovery = journal_recovery_path(cfg, operation_id)
    candidates = []
    for path in (active, recovery):
        if not path.exists():
            continue
        try:
            st = read_json_file(path)
            if st.get("operation_id") != operation_id:
                continue
            validate_journal_semantics(st)
            candidates.append((int(st["revision"]), path, st))
        except Exception:
            continue
    if not candidates:
        return None
    maxrev = max(x[0] for x in candidates)
    top = [x for x in candidates if x[0] == maxrev]
    if len(top) > 1 and any(x[2] != top[0][2] for x in top[1:]):
        raise RuntimeError(f"ambiguous conflicting local journals for operation {operation_id} revision {maxrev}")
    _, path, st = top[0]
    return path, st


def journal_effective_active(cfg: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    active = Path(cfg["state"]["fence_file"])
    if not active.exists():
        return None
    st = read_json_file(active)
    if int(st.get("schema_version", 0)) < 3:
        return active, st
    op = str(st.get("operation_id", ""))
    validate_journal_semantics(st)
    return journal_effective_for_operation(cfg, op)


def journal_is_terminal(state: dict[str, Any]) -> bool:
    return bool(state.get("rejoin_completed")) or state.get("journal_phase") == "rejoin-completed"


def assert_new_operation_allowed(cfg: dict[str, Any]) -> None:
    active = Path(cfg["state"]["fence_file"])
    if not active.exists():
        return
    try:
        st = read_json_file(active)
        if int(st.get("schema_version", 0)) < 3:
            raise RuntimeError("active pre-v3 journal exists; reconcile it before starting a new operation")
        validate_journal_semantics(st)
        effective = journal_effective_for_operation(cfg, str(st["operation_id"]))
        if effective is None:
            raise RuntimeError("active journal has no valid effective local state")
        _, est = effective
        if not journal_is_terminal(est):
            raise RuntimeError(f"unfinished HA operation {est['operation_id']} phase={est['journal_phase']} revision={est['revision']}")
    except Exception as exc:
        raise RuntimeError(f"cannot start a new HA operation: {exc}") from exc


class JournalQuorumError(RuntimeError):
    def __init__(self, state: dict[str, Any], report: dict[str, Any]):
        self.state = state
        self.report = report
        op = state.get("operation_id", "?")
        super().__init__(
            f"journal recovery quorum not met for operation {op} revision={state.get('revision','?')}: "
            f"required={report['required']} succeeded={len(report['succeeded'])} peers={report['succeeded']} failed={report['failed']}; "
            f"local checkpoint IS durable; do not roll back or start the next phase; reconcile with: "
            f"bf4-ha-journal-quorum-check --operation {op}"
        )


def journal_replicate(cfg: dict[str, Any], state: dict[str, Any], *, timeout: int = 12) -> dict[str, Any]:
    if int(state.get("schema_version", 0)) < 3:
        return {"required": 0, "succeeded": [], "failed": {}}
    validate_journal_semantics(state)
    here = controller_identity()
    controllers = cfg.get("controllers", {})
    required = int(cfg.get("state", {}).get("minimum_recovery_copies", 2))
    payload = journal_payload(state)
    succeeded: list[str] = []
    failed: dict[str, str] = {}
    for name, meta in controllers.items():
        if name == here:
            continue
        rc, out, err = ssh_input(meta["ip"], "/opt/bf4-ha/bin/bf4-ha-journal-receive", payload, timeout=timeout)
        if rc == 0:
            succeeded.append(name)
        else:
            failed[name] = err or out or f"exit={rc}"
    return {"required": required, "succeeded": succeeded, "failed": failed}


def journal_commit(path: Path, state: dict[str, Any], cfg: dict[str, Any], *, require_recovery: bool = True) -> dict[str, Any]:
    validate_journal_semantics(state)
    atomic_write_json(path, state, mode=0o600)
    report = journal_replicate(cfg, state)
    if require_recovery and len(report["succeeded"]) < int(report["required"]):
        raise JournalQuorumError(state, report)
    return report


def journal_commit_or_die(path: Path, state: dict[str, Any], cfg: dict[str, Any], *, require_recovery: bool = True) -> dict[str, Any]:
    try:
        return journal_commit(path, state, cfg, require_recovery=require_recovery)
    except JournalQuorumError as exc:
        print(f"NO-GO: {exc}", file=sys.stderr)
        raise SystemExit(40)

def atomic_write_json(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        dir=str(path.parent),
        text=True,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2, sort_keys=True)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        dir_fd = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def remove_file_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def vcenter_power_state(
    cfg: dict[str, Any],
    node_name: str,
    password: str,
) -> tuple[bool, str]:
    try:
        from pyVim.connect import Disconnect
        si, content, vm = vcenter_get_vm(cfg, node_name, password)
    except Exception as exc:
        return False, f"vCenter lookup failed: {type(exc).__name__}: {exc}"

    try:
        node = cfg["nodes"][node_name]
        if vm.runtime.host is None:
            return False, "target VM has no runtime ESXi host"
        if vm.runtime.host.name != node["esxi"]:
            return False, (
                f"ESXi mismatch: expected {node['esxi']}, got {vm.runtime.host.name}"
            )
        return True, str(vm.runtime.powerState)
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass


def sql_literal(value: str) -> str:
    """Return a PostgreSQL single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def conninfo_set_host(conninfo: str, new_host: str) -> str:
    """
    Replace/add the libpq conninfo host while preserving the other key/value
    tokens. This implementation is intentionally narrow for the BF4 cluster's
    simple keyword/value conninfo strings.
    """
    import shlex

    tokens = shlex.split(conninfo, posix=True)
    pairs: list[tuple[str, str]] = []

    for token in tokens:
        if "=" not in token:
            raise ValueError(f"invalid conninfo token without '=': {token!r}")
        key, value = token.split("=", 1)
        pairs.append((key, value))

    found = False
    rewritten: list[tuple[str, str]] = []

    for key, value in pairs:
        if key == "host":
            rewritten.append((key, new_host))
            found = True
        else:
            rewritten.append((key, value))

    if not found:
        rewritten.append(("host", new_host))

    def quote_value(value: str) -> str:
        # libpq keyword/value conninfo single-quote escaping
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    return " ".join(f"{key}={quote_value(value)}" for key, value in rewritten)


def dig_exact_a(server: str, name: str) -> tuple[int, list[str], str]:
    rc, out, err = run(
        ["dig", "+time=2", "+tries=1", f"@{server}", name, "A", "+short"],
        timeout=5,
    )
    return rc, [x.strip() for x in out.splitlines() if x.strip()], err



def nsupdate_tsig(
    server: str,
    zone: str,
    record: str,
    ttl: int,
    ip: str,
    key_file: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    script = (
        f"server {server}\n"
        f"zone {zone}\n"
        f"update delete {record} A\n"
        f"update add {record} {ttl} A {ip}\n"
        "send\n"
    )
    try:
        p = subprocess.run(
            ["nsupdate", "-k", key_file],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def nsupdate_tsig_prereq(
    server: str,
    zone: str,
    record: str,
    ip: str,
    key_file: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    script = (
        f"server {server}\n"
        f"zone {zone}\n"
        f"prereq yxrrset {record} A {ip}\n"
        "send\n"
    )
    try:
        p = subprocess.run(
            ["nsupdate", "-k", key_file],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"

def node_site(cfg: dict[str, Any], node_name: str) -> str | None:
    for site_code, site in cfg.get("sites", {}).items():
        if node_name in site.get("db_nodes", []):
            return site_code
    return None


def worker_site(cfg: dict[str, Any], worker_name: str) -> str | None:
    for site_code, site in cfg.get("sites", {}).items():
        if worker_name in site.get("workers", []):
            return site_code
    return None


def dns_site(cfg: dict[str, Any], dns_ip: str) -> str | None:
    for site_code, site in cfg.get("sites", {}).items():
        if dns_ip in site.get("dns_servers", []):
            return site_code
    return None


def db_client_ips(primary_ip: str, username: str = "bf4_serverwatcher") -> tuple[int, set[str], str]:
    sql = f"""
SELECT DISTINCT client_addr
FROM pg_stat_activity
WHERE backend_type = 'client backend'
  AND usename = '{username}'
  AND client_addr IS NOT NULL
ORDER BY client_addr;
"""
    rc, out, err = psql(primary_ip, sql)
    if rc != 0:
        return rc, set(), err or out
    rows = parse_pipe_rows(out)
    return 0, {row[0] for row in rows if row and row[0]}, ""


def db_client_session_rows(primary_ip: str, username: str = "bf4_serverwatcher") -> tuple[int, list[list[str]], str]:
    """Return per-client/state ServerWatcher session counts for HA diagnostics."""
    sql = f"""
SELECT client_addr, state, count(*)
FROM pg_stat_activity
WHERE backend_type = 'client backend'
  AND usename = '{username}'
  AND client_addr IS NOT NULL
GROUP BY client_addr, state
ORDER BY client_addr, state;
"""
    rc, out, err = psql(primary_ip, sql)
    if rc != 0:
        return rc, [], err or out
    return 0, parse_pipe_rows(out), ""


def ambiguous_worker_session_lines(cfg: dict[str, Any], primary_ip: str, sites) -> list[str]:
    """Human-readable lingering DB sessions for otherwise unreachable sites."""
    rc, rows, err = db_client_session_rows(primary_ip)
    if rc != 0:
        return [f"  session detail unavailable: {err}"]
    by_ip: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        if len(row) >= 3:
            by_ip.setdefault(row[0], []).append((row[1], row[2]))
    lines: list[str] = []
    for site_code in sorted(sites):
        for worker in cfg.get("sites", {}).get(site_code, {}).get("workers", []):
            meta = cfg.get("workers", {}).get(worker, {})
            ip = meta.get("ip")
            if not ip or ip not in by_ip:
                continue
            total = sum(int(count) for _state, count in by_ip[ip])
            states = ", ".join(f"{state}={count}" for state, count in by_ip[ip])
            lines.append(f"  {worker:<8} {ip:<15} sessions={total} ({states})")
    return lines


def classify_site_isolation(cfg, site_code, db_states, worker_reachability, dns_reachability):
    site = cfg.get("sites", {}).get(site_code, {})
    checks = []
    for node in site.get("db_nodes", []):
        checks.append(not db_states.get(node, {}).get("reachable", False))
    for worker in site.get("workers", []):
        checks.append(not worker_reachability.get(worker, False))
    for dns_ip in site.get("dns_servers", []):
        checks.append(not dns_reachability.get(dns_ip, False))
    return bool(checks) and all(checks)



def failure_domain_snapshot(
    cfg: dict[str, Any],
    primary_name: str | None = None,
) -> dict[str, Any]:
    """
    Build one conservative live view of DB/worker/DNS failure domains.

    A site is SAFE_ISOLATED only when every configured endpoint in that site is
    unreachable from the controller AND none of that site's worker IPs still
    appears as a bf4_serverwatcher PostgreSQL client on the current primary.

    If the site is otherwise isolated but any worker DB session remains, the
    site is AMBIGUOUS and must block destructive execution.
    """
    db_states, db_problems = discover_roles(cfg)

    if primary_name is None:
        primary_name = current_primary(db_states)

    client_ips: set[str] = set()
    client_query_error = ""
    if (
        primary_name
        and primary_name in cfg["nodes"]
        and db_states.get(primary_name, {}).get("postgresql")
    ):
        rc, client_ips, client_query_error = db_client_ips(
            cfg["nodes"][primary_name]["ip"]
        )
        if rc != 0:
            client_ips = set()

    worker_reachability: dict[str, bool] = {}
    worker_errors: dict[str, str] = {}
    for name, meta in cfg["workers"].items():
        rc, out, err = ssh(meta["ip"], "hostname")
        worker_reachability[name] = rc == 0
        if rc != 0:
            worker_errors[name] = err or out or "unreachable"

    record = cfg["dns"].get("service_record", cfg["service_dns"])
    dns_reachability: dict[str, bool] = {}
    dns_values: dict[str, list[str]] = {}
    dns_errors: dict[str, str] = {}
    for dns_ip in cfg["dns"]["verify_servers"]:
        rc, values, err = dig_exact_a(dns_ip, record)
        dns_reachability[dns_ip] = rc == 0
        dns_values[dns_ip] = values
        if rc != 0:
            dns_errors[dns_ip] = err or "unreachable"

    safe_isolated_sites: set[str] = set()
    ambiguous_isolated_sites: set[str] = set()
    partial_sites: set[str] = set()

    for site_code, site in cfg.get("sites", {}).items():
        endpoint_checks: list[bool] = []

        for node in site.get("db_nodes", []):
            endpoint_checks.append(
                bool(db_states.get(node, {}).get("reachable", False))
            )
        for worker in site.get("workers", []):
            endpoint_checks.append(worker_reachability.get(worker, False))
        for dns_ip in site.get("dns_servers", []):
            endpoint_checks.append(dns_reachability.get(dns_ip, False))

        all_unreachable = bool(endpoint_checks) and not any(endpoint_checks)

        site_worker_ips = {
            cfg["workers"][w]["ip"]
            for w in site.get("workers", [])
            if w in cfg["workers"]
        }
        lingering_clients = sorted(site_worker_ips & client_ips)

        if all_unreachable:
            if lingering_clients:
                ambiguous_isolated_sites.add(site_code)
            else:
                safe_isolated_sites.add(site_code)
        elif endpoint_checks and not all(endpoint_checks):
            partial_sites.add(site_code)

    deferred_db_nodes = sorted({
        node
        for site_code in safe_isolated_sites
        for node in cfg["sites"][site_code].get("db_nodes", [])
    })
    deferred_workers = sorted({
        worker
        for site_code in safe_isolated_sites
        for worker in cfg["sites"][site_code].get("workers", [])
    })
    deferred_dns_servers = sorted({
        dns_ip
        for site_code in safe_isolated_sites
        for dns_ip in cfg["sites"][site_code].get("dns_servers", [])
    })

    return {
        "db_states": db_states,
        "db_problems": db_problems,
        "primary": primary_name,
        "db_client_ips": client_ips,
        "db_client_query_error": client_query_error,
        "worker_reachability": worker_reachability,
        "worker_errors": worker_errors,
        "dns_reachability": dns_reachability,
        "dns_values": dns_values,
        "dns_errors": dns_errors,
        "safe_isolated_sites": safe_isolated_sites,
        "ambiguous_isolated_sites": ambiguous_isolated_sites,
        "partial_sites": partial_sites,
        "deferred_db_nodes": deferred_db_nodes,
        "deferred_workers": deferred_workers,
        "deferred_dns_servers": deferred_dns_servers,
    }


def site_for_worker(cfg: dict[str, Any], worker_name: str) -> str | None:
    for code, site in cfg.get("sites", {}).items():
        if worker_name in site.get("workers", []):
            return code
    return None


def site_for_node(cfg: dict[str, Any], node_name: str) -> str | None:
    for code, site in cfg.get("sites", {}).items():
        if node_name in site.get("db_nodes", []):
            return code
    return None


def site_for_dns(cfg: dict[str, Any], dns_ip: str) -> str | None:
    for code, site in cfg.get("sites", {}).items():
        if dns_ip in site.get("dns_servers", []):
            return code
    return None


def vcenter_poweron_and_verify(
    cfg: dict[str, Any],
    node_name: str,
    password: str,
    timeout: int = 120,
) -> tuple[bool, str]:
    """Power on a configured VM and independently verify poweredOn state."""
    import time
    from pyVim.connect import Disconnect
    from pyVmomi import vim

    try:
        si, content, vm = vcenter_get_vm(cfg, node_name, password)
    except Exception as exc:
        return False, f"vCenter lookup failed: {type(exc).__name__}: {exc}"

    try:
        node = cfg["nodes"][node_name]
        if vm.runtime.host is None:
            return False, "target VM has no runtime ESXi host"
        if vm.runtime.host.name != node["esxi"]:
            return False, (
                f"ESXi mismatch: expected {node['esxi']}, got {vm.runtime.host.name}"
            )
        if str(vm.runtime.powerState) != "poweredOff":
            return False, f"target must be poweredOff; current state={vm.runtime.powerState}"

        session = content.sessionManager.currentSession
        if session is None:
            return False, "no authenticated vCenter session"
        priv = content.authorizationManager.HasPrivilegeOnEntity(
            entity=vm,
            sessionId=session.key,
            privId=["System.View", "VirtualMachine.Interact.PowerOn"],
        )
        if not bool(priv[0]) or not bool(priv[1]):
            return False, "required vCenter power-on privileges are missing"

        task = vm.PowerOnVM_Task()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if task.info.state == vim.TaskInfo.State.success:
                break
            if task.info.state == vim.TaskInfo.State.error:
                return False, f"vCenter PowerOn task failed: {task.info.error}"
            time.sleep(1)
        else:
            return False, "timed out waiting for vCenter PowerOn task"

        deadline = time.time() + 30
        while time.time() < deadline:
            if str(vm.runtime.powerState) == "poweredOn":
                return True, "poweredOn"
            time.sleep(1)
        return False, "PowerOn task succeeded but VM did not reach poweredOn state"
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass

# v0.11.6 direct-ESXi fencing helpers
def prompt_esxi_password(username: str = "root") -> str:
    return prompt_secret(f"ESXi {username} password: ")


def esxi_get_vm(cfg: dict[str, Any], node_name: str, password: str):
    import ssl
    from pyVim.connect import SmartConnect
    from pyVmomi import vim
    esxi = cfg.get("esxi", {})
    mapping = esxi.get("db_vm_map", {}).get(node_name)
    if not mapping:
        raise RuntimeError(f"no direct ESXi mapping for {node_name}")
    host_key = mapping["esxi_host"]
    host = esxi.get("hosts", {}).get(host_key)
    if not host:
        raise RuntimeError(f"mapped ESXi host {host_key!r} missing")
    expected_vm = cfg["nodes"][node_name]["vm_name"]
    if mapping.get("vm_name") != expected_vm:
        raise RuntimeError("direct ESXi mapping VM name disagrees with node vm_name")
    si = SmartConnect(host=host["ip"], user=esxi.get("username", "root"), pwd=password,
                      sslContext=ssl._create_unverified_context())
    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        matches = [vm for vm in view.view if vm.name == expected_vm]
    finally:
        view.Destroy()
    if len(matches) != 1:
        from pyVim.connect import Disconnect
        Disconnect(si)
        raise RuntimeError(f"exact VM match count={len(matches)} for {expected_vm!r}")
    vm = matches[0]
    if vm.runtime.host is None:
        from pyVim.connect import Disconnect
        Disconnect(si)
        raise RuntimeError("target VM has no runtime ESXi host")
    # Direct connection itself is the placement proof; additionally require configured host identity.
    actual = vm.runtime.host.name
    expected_names = {host.get("name"), host_key, host.get("ip")}
    if actual not in expected_names:
        from pyVim.connect import Disconnect
        Disconnect(si)
        raise RuntimeError(f"ESXi mismatch: configured {host_key}/{host['ip']}, runtime {actual}")
    return si, content, vm, host_key, host


def esxi_power_state(cfg: dict[str, Any], node_name: str, password: str) -> tuple[bool, str]:
    from pyVim.connect import Disconnect
    try:
        si, content, vm, host_key, host = esxi_get_vm(cfg, node_name, password)
    except Exception as exc:
        return False, f"direct ESXi lookup failed: {type(exc).__name__}: {exc}"
    try:
        return True, str(vm.runtime.powerState)
    finally:
        Disconnect(si)


def esxi_poweroff_and_verify(cfg: dict[str, Any], node_name: str, password: str, timeout: int = 120) -> tuple[bool, str]:
    import time
    from pyVim.connect import Disconnect
    from pyVmomi import vim
    try:
        si, content, vm, host_key, host = esxi_get_vm(cfg, node_name, password)
    except Exception as exc:
        return False, f"direct ESXi lookup failed: {type(exc).__name__}: {exc}"
    try:
        if str(vm.runtime.powerState) != "poweredOn":
            return False, f"target must be poweredOn before live fencing; current state={vm.runtime.powerState}"
        task = vm.PowerOffVM_Task()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if task.info.state == vim.TaskInfo.State.success: break
            if task.info.state == vim.TaskInfo.State.error: return False, f"direct ESXi PowerOff task failed: {task.info.error}"
            time.sleep(1)
        else: return False, "timed out waiting for direct ESXi PowerOff task"
        deadline = time.time() + 30
        while time.time() < deadline:
            if str(vm.runtime.powerState) == "poweredOff": return True, "poweredOff"
            time.sleep(1)
        return False, "PowerOff task succeeded but VM did not reach poweredOff state"
    finally:
        Disconnect(si)


def esxi_poweron_and_verify(cfg: dict[str, Any], node_name: str, password: str, timeout: int = 120) -> tuple[bool, str]:
    import time
    from pyVim.connect import Disconnect
    from pyVmomi import vim
    try:
        si, content, vm, host_key, host = esxi_get_vm(cfg, node_name, password)
    except Exception as exc:
        return False, f"direct ESXi lookup failed: {type(exc).__name__}: {exc}"
    try:
        if str(vm.runtime.powerState) != "poweredOff":
            return False, f"target must be poweredOff; current state={vm.runtime.powerState}"
        task = vm.PowerOnVM_Task()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if task.info.state == vim.TaskInfo.State.success: break
            if task.info.state == vim.TaskInfo.State.error: return False, f"direct ESXi PowerOn task failed: {task.info.error}"
            time.sleep(1)
        else: return False, "timed out waiting for direct ESXi PowerOn task"
        deadline = time.time() + 30
        while time.time() < deadline:
            if str(vm.runtime.powerState) == "poweredOn": return True, "poweredOn"
            time.sleep(1)
        return False, "PowerOn task succeeded but VM did not reach poweredOn state"
    finally:
        Disconnect(si)


def fence_backend_power_state(cfg: dict[str, Any], state: dict[str, Any], node_name: str, password: str) -> tuple[bool, str]:
    backend = state.get("fencing_backend", "vcenter")
    if backend == "direct-esxi":
        return esxi_power_state(cfg, node_name, password)
    if backend == "vcenter":
        return vcenter_power_state(cfg, node_name, password)
    return False, f"unsupported fencing backend {backend!r}"

def prompt_fence_backend_password(cfg: dict[str, Any], state: dict[str, Any]) -> str:
    if state.get("fencing_backend", "vcenter") == "direct-esxi":
        return prompt_esxi_password(cfg.get("esxi", {}).get("username", "root"))
    return prompt_vcenter_password(cfg["vcenter"]["username"])
