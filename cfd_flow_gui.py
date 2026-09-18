import os
import sys
import glob
import re
import math
import json
import csv
import locale
from datetime import datetime
import shutil
import subprocess
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# Initialize locale to match the user's OS settings
try:
    locale.setlocale(locale.LC_ALL, "")
except Exception:
    pass

if sys.platform == "darwin":
    # On macOS, GUI apps launched from Finder lack shell LANG environment variables,
    # causing python to default to C locale. Query AppleLocale to apply user's system locale.
    _curr_loc = locale.getlocale()[0]
    if not _curr_loc or _curr_loc.startswith("C") or _curr_loc == "POSIX":
        try:
            _apple_out = subprocess.check_output(
                ["defaults", "read", "-g", "AppleLocale"],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip().split("@")[0]
            for _cand in (f"{_apple_out}.UTF-8", _apple_out, "de_DE.UTF-8", "en_US.UTF-8"):
                try:
                    locale.setlocale(locale.LC_ALL, _cand)
                    break
                except Exception:
                    continue
        except Exception:
            pass

# Configuration file location for persistent settings
CONFIG_DIR = os.path.expanduser("~/.config/venturi_post")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
OLD_CONFIG_FILES = [
    os.path.expanduser("~/.config/openfoam_venturi_nozzle_analyze/config.json"),
    os.path.expanduser("~/.config/cfd_flow_gui/config.json"),
]

# Ensure standard binary paths are in PATH (macOS GUI apps launched via Finder lack /usr/local/bin, /opt/homebrew/bin, etc.)
_extra_paths = [
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    os.path.expanduser("~/.docker/bin"),
    "/Applications/Docker.app/Contents/Resources/bin",
]
_path_parts = os.environ.get("PATH", "").split(os.pathsep)
for _p in _extra_paths:
    if _p not in _path_parts and os.path.exists(_p):
        _path_parts.insert(0, _p)
os.environ["PATH"] = os.pathsep.join(_path_parts)

class CFDFlowGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("OpenFoam Venturi Nozzle Post Processor")
        self.root.geometry("860x750")
        self.root.minsize(800, 580)

        # Load saved configuration or defaults
        cfg = self._load_config()

        # Initialize variables
        self.case_path_var = tk.StringVar(value=cfg.get("case_path", ""))
        self.inlet_name_var = tk.StringVar(value=cfg.get("inlet_name", "inlet"))
        self.open_name_var = tk.StringVar(value=cfg.get("open_name", "open"))
        self.plane_z_var = tk.StringVar(value=cfg.get("plane_z", "90"))
        self.normal_var = tk.StringVar(value=cfg.get("normal", "0 0 1"))
        self.p_gauge_var = tk.StringVar(value=cfg.get("p_gauge", "2.0"))
        p_amb_init = cfg.get("p_amb", "1.000")
        try:
            p_amb_init = f"{float(str(p_amb_init).replace(',', '.')):.3f}"
        except Exception:
            p_amb_init = "1.000"
        self.p_amb_var = tk.StringVar(value=p_amb_init)
        self.temp_c_var = tk.StringVar(value=cfg.get("temp_c", "20.0"))
        self.docker_img_var = tk.StringVar(value=cfg.get("docker_img", "cfdof-openfoam:opencfd-mac"))
        self.csv_path_var = tk.StringVar(value=cfg.get("csv_path", ""))
        self.last_results = None

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        if self.case_path_var.get().strip():
            self.auto_load_case_parameters(silent=True)

    def _load_config(self):
        defaults = {
            "case_path": "",
            "inlet_name": "inlet",
            "open_name": "open",
            "plane_z": "90",
            "normal": "0 0 1",
            "p_gauge": "2.0",
            "p_amb": "1.000",
            "temp_c": "20.0",
            "docker_img": "cfdof-openfoam:opencfd-mac",
            "csv_path": "",
        }
        cfg_path = None
        if os.path.exists(CONFIG_FILE):
            cfg_path = CONFIG_FILE
        else:
            for old_p in OLD_CONFIG_FILES:
                if os.path.exists(old_p):
                    cfg_path = old_p
                    break

        if cfg_path:
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        defaults.update(saved)
            except Exception as e:
                print(f"Notice: Could not load {cfg_path}: {e}")
        return defaults

    def _save_config(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            data = {
                "case_path": self.case_path_var.get().strip(),
                "inlet_name": self.inlet_name_var.get().strip(),
                "open_name": self.open_name_var.get().strip(),
                "plane_z": self.plane_z_var.get().strip(),
                "normal": self.normal_var.get().strip(),
                "p_gauge": self.p_gauge_var.get().strip(),
                "p_amb": self.p_amb_var.get().strip(),
                "temp_c": self.temp_c_var.get().strip(),
                "docker_img": self.docker_img_var.get().strip(),
                "csv_path": self.csv_path_var.get().strip(),
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Notice: Could not save {CONFIG_FILE}: {e}")

    def _on_close(self):
        self._save_config()
        self.root.destroy()

    def _open_url(self, url):
        def _run():
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", url])
                elif sys.platform == "win32":
                    os.startfile(url)
                else:
                    subprocess.Popen(["xdg-open", url])
            except Exception:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
        threading.Thread(target=_run, daemon=True).start()

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # Do not use name="help" on macOS: it binds to Apple Help Book/Carbon Help Manager,
        # which injects a "VenturiPost Help" search hook that hangs without an Apple Help bundle.
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)

        help_menu.add_command(
            label="VenturiPost GitHub Repository",
            command=lambda: self._open_url("https://github.com/soylentOrange/cfd-flow-gui")
        )
        help_menu.add_command(
            label="CfdOF OpenFOAM Docker GitHub (kktse)",
            command=lambda: self._open_url("https://github.com/kktse/cfdof-openfoam-docker")
        )
        help_menu.add_separator()
        help_menu.add_command(
            label="Documentation (README)",
            command=lambda: self._open_url("https://github.com/soylentOrange/cfd-flow-gui#readme")
        )
        help_menu.add_command(
            label="About VenturiPost",
            command=self._show_about
        )

        try:
            self.root.createcommand("tkAboutDialog", self._show_about)
        except Exception:
            pass

    def _show_about(self):
        # Defer dialog creation slightly to exit the macOS Aqua menu tracking loop cleanly
        self.root.after(50, self._create_about_dialog)

    def _create_about_dialog(self):
        about_win = tk.Toplevel(self.root)
        about_win.title("About VenturiPost")
        about_win.resizable(False, False)
        about_win.transient(self.root)

        frame = ttk.Frame(about_win, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="⚡ VenturiPost", font=("Helvetica", 16, "bold")).pack(pady=(0, 4))
        ttk.Label(frame, text="OpenFOAM Venturi Nozzle Post Processor", font=("Helvetica", 11, "italic")).pack(pady=(0, 10))

        ttk.Label(
            frame, 
            text="Post-processing tool for compressible ejector & venturi nozzle CFD cases.", 
            font=("Helvetica", 10),
            justify="center"
        ).pack(pady=(0, 14))

        ttk.Label(frame, text="GitHub Repositories:", font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 4))

        link1 = ttk.Label(frame, text="• VenturiPost (cfd-flow-gui)", foreground="#0066cc", cursor="pointinghand")
        link1.pack(anchor="w", pady=2)
        link1.bind("<Button-1>", lambda e: self._open_url("https://github.com/soylentOrange/cfd-flow-gui"))

        link2 = ttk.Label(frame, text="• CfdOF OpenFOAM Docker (kktse)", foreground="#0066cc", cursor="pointinghand")
        link2.pack(anchor="w", pady=2)
        link2.bind("<Button-1>", lambda e: self._open_url("https://github.com/kktse/cfdof-openfoam-docker"))

        btn_close = ttk.Button(frame, text="Close", command=about_win.destroy)
        btn_close.pack(pady=(18, 0))

        # Center dialog relative to main window
        about_win.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        dw = about_win.winfo_reqwidth()
        dh = about_win.winfo_reqheight()
        x = rx + max(0, (rw - dw) // 2)
        y = ry + max(0, (rh - dh) // 2)
        about_win.geometry(f"+{x}+{y}")

    def _build_ui(self):
        self._build_menu()

        W_BTN = 18
        W = 21
        W_DOCKER = 42

        # 1. Case Directory
        frame_dir = ttk.LabelFrame(self.root, text=" 1. Case Directory ", padding=10)
        frame_dir.pack(fill="x", padx=10, pady=5)

        entry_dir = ttk.Entry(frame_dir, textvariable=self.case_path_var)
        entry_dir.grid(row=0, column=0, columnspan=3, sticky="ew", padx=(0, 5))

        btn_browse = ttk.Button(frame_dir, text="Browse...", width=W_BTN, command=self._browse_dir)
        btn_browse.grid(row=0, column=3, sticky="ew", padx=5)

        # 2. Parameters
        frame_params = ttk.LabelFrame(self.root, text=" 2. Geometry & Pressure Settings ", padding=10)
        frame_params.pack(fill="x", padx=10, pady=5)

        # Row 0: Docker Image & Reload from Case
        ttk.Label(frame_params, text="Docker Image:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frame_params, textvariable=self.docker_img_var, width=W_DOCKER).grid(row=0, column=1, columnspan=2, sticky="w", padx=5)

        btn_autodetect = ttk.Button(
            frame_params, 
            text="🔄 Reload from Case", 
            width=W_BTN,
            command=self.auto_load_case_parameters
        )
        btn_autodetect.grid(row=0, column=3, sticky="ew", padx=5, pady=3)

        # Row 1: Patches
        ttk.Label(frame_params, text="Inlet Patch Name:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame_params, textvariable=self.inlet_name_var, width=W).grid(row=1, column=1, sticky="w", padx=5)

        ttk.Label(frame_params, text="Open Patch Name:").grid(row=1, column=2, sticky="w", padx=(15, 0), pady=3)
        ttk.Entry(frame_params, textvariable=self.open_name_var, width=W).grid(row=1, column=3, sticky="ew", padx=5)

        # Row 2: Geometry & Temperature
        ttk.Label(frame_params, text="Plane Z-Distance [mm]:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frame_params, textvariable=self.plane_z_var, width=W).grid(row=2, column=1, sticky="w", padx=5)

        ttk.Label(frame_params, text="Temperature [°C]:").grid(row=2, column=2, sticky="w", padx=(15, 0), pady=3)
        ttk.Entry(frame_params, textvariable=self.temp_c_var, width=W).grid(row=2, column=3, sticky="ew", padx=5)

        # Row 3: Pressure Parameters
        ttk.Label(frame_params, text="Inlet Gauge Pressure [bar g]:").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(frame_params, textvariable=self.p_gauge_var, width=W).grid(row=3, column=1, sticky="w", padx=5)

        ttk.Label(frame_params, text="Ambient Pressure [bar abs]:").grid(row=3, column=2, sticky="w", padx=(15, 0), pady=3)
        ttk.Entry(frame_params, textvariable=self.p_amb_var, width=W).grid(row=3, column=3, sticky="ew", padx=5)

        # 3. Status and Output Area
        frame_out = ttk.LabelFrame(self.root, text=" 3. Analysis & Protocol ", padding=10)
        frame_out.pack(fill="both", expand=True, padx=10, pady=5)

        frame_out_bar = ttk.Frame(frame_out)
        frame_out_bar.pack(fill="x", pady=(0, 6))

        self.btn_run_eval = ttk.Button(
            frame_out_bar, 
            text="⚡ Start Analysis", 
            width=W_BTN,
            command=self.start_evaluation_thread
        )
        self.btn_run_eval.grid(row=0, column=0, sticky="w")

        self.btn_export_csv = ttk.Button(
            frame_out_bar, 
            text="📊 Export to CSV...", 
            width=W_BTN,
            command=self.export_to_csv
        )
        self.btn_export_csv.grid(row=0, column=1, columnspan=2, sticky="")

        self.btn_clear = ttk.Button(
            frame_out_bar, 
            text="🗑️ Clear Console", 
            width=W_BTN,
            command=self.clear_console
        )
        self.btn_clear.grid(row=0, column=3, sticky="ew", padx=5)

        # Synchronize column widths across Section 1, 2, and 3 for pixel-perfect vertical alignment
        self.root.update_idletasks()
        col_widths = [0, 0, 0, 0]
        for col in range(4):
            w1 = frame_dir.grid_bbox(col, 0)[2]
            w2 = frame_params.grid_bbox(col, 0)[2]
            w3 = frame_out_bar.grid_bbox(col, 0)[2]
            col_widths[col] = max(w1, w2, w3)

        for col in range(4):
            frame_dir.columnconfigure(col, minsize=col_widths[col])
            frame_params.columnconfigure(col, minsize=col_widths[col])
            frame_out_bar.columnconfigure(col, minsize=col_widths[col])

        self.txt_output = ScrolledText(frame_out, wrap="word", font=("Courier", 11))
        self.txt_output.pack(fill="both", expand=True)

        self.log("Ready. Please select the case directory.")

    def log(self, text):
        self.txt_output.insert("end", text + "\n")
        self.txt_output.see("end")

    def clear_console(self):
        self.txt_output.delete("1.0", "end")

    def _get_case_name(self, case_dir):
        if not case_dir:
            return "unknown_case"
        norm = os.path.normpath(case_dir.strip())
        base = os.path.basename(norm)
        if base.lower() == "case":
            parent = os.path.basename(os.path.dirname(norm))
            return parent if parent else base
        return base

    def export_to_csv(self):
        if not self.last_results:
            messagebox.showwarning(
                "Notice", 
                "No analysis results available to export yet.\nPlease run an analysis first.",
                parent=self.root
            )
            return

        case_name = self.last_results.get("Case Name", "case")

        # Determine initial directory and filename
        current_csv = self.csv_path_var.get().strip()
        initial_dir = ""
        initial_file = "venturi_results.csv"

        if current_csv:
            initial_dir = os.path.dirname(current_csv)
            initial_file = os.path.basename(current_csv)
        else:
            case_dir = self.case_path_var.get().strip()
            if case_dir:
                norm = os.path.normpath(case_dir)
                initial_dir = os.path.dirname(norm)
            if not initial_dir:
                initial_dir = os.path.expanduser("~")

        try:
            target_file = filedialog.asksaveasfilename(
                title="Select or Create CSV File for Results Export",
                initialdir=initial_dir,
                initialfile=initial_file,
                defaultextension=".csv",
                filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
                parent=self.root
            )
        except Exception as e:
            self.log(f"Error opening file dialog: {e}")
            return

        if not target_file:
            return

        self.csv_path_var.set(target_file)
        self._save_config()

        try:
            self._append_results_to_csv(target_file, self.last_results)
            delim, dec = self._get_csv_format_settings(target_file)
            self.log("=" * 64)
            self.log(f"✓ Results for case '{case_name}' appended to CSV:")
            self.log(f"  -> File: {target_file}")
            self.log(f"  -> Locale format: delimiter='{delim}', decimal='{dec}'")
            self.log("=" * 64)
            messagebox.showinfo(
                "Export Successful",
                f"Results for case '{case_name}' were successfully appended to:\n\n{target_file}\n\nFormat: delimiter='{delim}', decimal='{dec}'",
                parent=self.root
            )
        except (PermissionError, OSError) as e:
            err_msg = (
                f"Could not write to file:\n\n{target_file}\n\n"
                f"The file appears to be open in Microsoft Excel or another application (file locked).\n\n"
                f"Please close the file in Excel and try exporting again."
            )
            self.log("=" * 64)
            self.log(f"FILE ACCESS / LOCK ERROR: {e}")
            self.log(f"  -> {err_msg}")
            self.log("=" * 64)
            messagebox.showerror("File Locked by Excel", err_msg, parent=self.root)
        except Exception as e:
            self.log(f"ERROR exporting CSV: {e}")
            messagebox.showerror("Export Error", f"Failed to write to CSV file:\n{e}", parent=self.root)

    def _get_csv_format_settings(self, csv_file=None):
        """Determines CSV delimiter and decimal separator based on existing file or system locale.
        
        Excel conventions:
        - In European/German locale (decimal separator = ','): delimiter is ';'
        - In US/UK locale (decimal separator = '.'): delimiter is ','
        """
        # 1. If file exists and is non-empty, preserve its established delimiter
        if csv_file and os.path.isfile(csv_file) and os.path.getsize(csv_file) > 0:
            try:
                with open(csv_file, "r", encoding="utf-8-sig", errors="ignore") as f:
                    first_line = f.readline()
                    if ";" in first_line:
                        return ";", ","
                    elif "," in first_line:
                        return ",", "."
            except Exception:
                pass

        # 2. Derive from system locale
        try:
            conv = locale.localeconv()
            dec_point = conv.get("decimal_point", ".")
        except Exception:
            dec_point = "."

        if dec_point == ",":
            return ";", ","
        else:
            return ",", "."

    def _append_results_to_csv(self, csv_file, res):
        fieldnames = [
            # 1. Case name & Timestamp
            "Case Name",
            "Timestamp",
            # 2. Primary key metrics: Nl/min for plane & inlet, Flow amplification factor
            "Plane Flow ISO [Nl/min]",
            "Plane Flow DIN [Nl/min]",
            "Inlet Flow ISO [Nl/min]",
            "Inlet Flow DIN [Nl/min]",
            "Flow Amplification Factor",
            # 3. Velocities & Pressures
            "Center Probe |U| [m/s]",
            "Center Probe Velocity [km/h]",
            "Center Probe Ux [m/s]",
            "Center Probe Uy [m/s]",
            "Center Probe Uz [m/s]",
            "Inlet Gauge Pressure [bar g]",
            "Inlet Abs Pressure [bar abs]",
            "Ambient Pressure [bar abs]",
            "Center Probe Static Pressure [bar abs]",
            "Center Probe Static Pressure [Pa]",
            # 4. Other flow & geometry parameters
            "Plane Volume Flow [m3/s]",
            "Plane Mass Flow [kg/s]",
            "Inlet Volume Flow [m3/s]",
            "Inlet Mass Flow [kg/s]",
            "Plane Z [mm]",
            "Temperature [C]",
            "Inlet Patch",
            "Open Patch",
            # 5. Automatically computed densities & Case path
            "Inlet Density [kg/m3]",
            "Ambient Density [kg/m3]",
            "Case Directory",
        ]

        delimiter, decimal_sep = self._get_csv_format_settings(csv_file)

        text_columns = {"Case Name", "Timestamp", "Case Directory", "Inlet Patch", "Open Patch"}
        row_to_write = {}
        for k in fieldnames:
            v = res.get(k, "")
            if v is None:
                row_to_write[k] = ""
            elif k in text_columns:
                row_to_write[k] = str(v)
            else:
                # Format numbers according to locale decimal separator to prevent Excel from interpreting as dates
                val_str = str(v).strip()
                if decimal_sep == ",":
                    row_to_write[k] = val_str.replace(".", ",")
                else:
                    row_to_write[k] = val_str.replace(",", ".")

        file_exists = os.path.isfile(csv_file)
        write_header = not file_exists or os.path.getsize(csv_file) == 0

        # utf-8-sig ensures Excel correctly handles formatting & characters
        with open(csv_file, mode="a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
            if write_header:
                writer.writeheader()
            writer.writerow(row_to_write)

    def _browse_dir(self):
        initial = self.case_path_var.get().strip() or os.path.expanduser("~")
        folder = filedialog.askdirectory(initialdir=initial)
        if folder:
            self.case_path_var.set(folder)
            self.auto_load_case_parameters()
            self._save_config()

    def _parse_foam_dict(self, file_path, field_type="p"):
        """Parses OpenFOAM dictionaries separately for pressure ('p') or temperature ('T')."""
        if not os.path.exists(file_path):
            return None, {}
        try:
            with open(file_path, "r", errors="ignore") as f:
                content = f.read()

            content = re.sub(r'//.*', '', content)
            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

            # internalField (extract explicit numerical values only)
            internal_val = None
            m_int = re.search(r'internalField\s+(?:uniform\s+)?([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)', content)
            if m_int:
                internal_val = float(m_int.group(1))

            boundaries = {}
            m_bf = re.search(r'boundaryField\s*\{', content)
            if m_bf:
                start = m_bf.end()
                depth = 1
                end = start
                for i in range(start, len(content)):
                    if content[i] == '{':
                        depth += 1
                    elif content[i] == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                bf_content = content[start:end]

                pos = 0
                while pos < len(bf_content):
                    m = re.search(r'([a-zA-Z0-9_\-".*]+)\s*\{', bf_content[pos:])
                    if not m:
                        break
                    patch_name = m.group(1).strip('"\'')
                    b_start = pos + m.end()
                    d = 1
                    b_end = b_start
                    for j in range(b_start, len(bf_content)):
                        if bf_content[j] == '{':
                            d += 1
                        elif bf_content[j] == '}':
                            d -= 1
                            if d == 0:
                                b_end = j
                                break
                    block_body = bf_content[b_start:b_end]

                    # Field-specific search patterns
                    if field_type == "p":
                        keywords = [
                            r'\bp\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
                            r'\bp0\s+(?:uniform\s+)?([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
                            r'\bfieldInf\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
                            r'\b(?:value|inletValue)\s+uniform\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
                            r'\buniform\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
                        ]
                    else:  # field_type == "T"
                        keywords = [
                            r'\bT\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
                            r'\b(?:value|inletValue)\s+uniform\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
                            r'\buniform\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
                        ]

                    val = None
                    for pattern in keywords:
                        m_v = re.search(pattern, block_body)
                        if m_v:
                            val = float(m_v.group(1))
                            break

                    boundaries[patch_name] = val
                    pos = b_end + 1

            return internal_val, boundaries
        except Exception:
            return None, {}

    def auto_load_case_parameters(self, silent=False):
        """Reads p and T automatically from case files."""
        case_dir = self.case_path_var.get().strip()
        if not case_dir:
            if not silent:
                messagebox.showwarning("Notice", "Please select a case directory first.")
            return

        inlet_name = self.inlet_name_var.get().strip().lower()
        open_name = self.open_name_var.get().strip().lower()

        p_paths = [os.path.join(case_dir, "0", "p"), os.path.join(case_dir, "0.orig", "p")]
        t_paths = [os.path.join(case_dir, "0", "T"), os.path.join(case_dir, "0.orig", "T")]

        p_file = next((p for p in p_paths if os.path.exists(p)), None)
        t_file = next((p for p in t_paths if os.path.exists(p)), None)

        if not p_file and not t_file:
            if not silent:
                messagebox.showwarning("Notice", "Neither 0/p nor 0/T found in case folder.")
            return

        updates = []

        # 1. Determine pressures (from 0/p)
        if p_file:
            p_int, p_bounds = self._parse_foam_dict(p_file, field_type="p")

            p_inlet_pa = None
            for name, val in p_bounds.items():
                if name.lower() == inlet_name and val is not None:
                    p_inlet_pa = val
                    break

            p_amb_pa = None
            found_amb_name = None
            candidates_p = [open_name] if open_name else []
            candidates_p += [c for c in ["open", "outlet", "atmosphere", "ambient", "farfield"] if c not in candidates_p]

            for candidate in candidates_p:
                for name, val in p_bounds.items():
                    if candidate in name.lower() and val is not None:
                        p_amb_pa = val
                        found_amb_name = name
                        break
                if p_amb_pa is not None:
                    break

            if p_amb_pa is not None:
                p_amb_bar = p_amb_pa / 1e5
                self.p_amb_var.set(f"{p_amb_bar:.3f}")
                updates.append(f"Ambient pressure: {p_amb_bar:.4f} bar abs (from patch '{found_amb_name}')")
            elif p_int is not None:
                p_amb_bar = p_int / 1e5
                self.p_amb_var.set(f"{p_amb_bar:.3f}")
                updates.append(f"Ambient pressure: {p_amb_bar:.4f} bar abs (from internalField)")

            if p_inlet_pa is not None and p_amb_pa is not None:
                p_gauge_bar = (p_inlet_pa - p_amb_pa) / 1e5
                self.p_gauge_var.set(f"{p_gauge_bar:.3f}")
                updates.append(f"Inlet gauge pressure: {p_gauge_bar:.3f} bar g (difference '{inlet_name}' - '{found_amb_name}')")

        # 2. Determine temperature (primary from 0/T, fallback on 0/p)
        t_kelvin = None
        source_file = None

        if t_file:
            t_int, t_bounds = self._parse_foam_dict(t_file, field_type="T")
            for name, val in t_bounds.items():
                if name.lower() == inlet_name and val is not None:
                    t_kelvin = val
                    source_file = "0/T"
                    break
            if t_kelvin is None:
                candidates_t = [open_name] if open_name else []
                candidates_t += [c for c in ["open", "outlet", "atmosphere", "ambient"] if c not in candidates_t]
                for candidate in candidates_t:
                    for name, val in t_bounds.items():
                        if candidate in name.lower() and val is not None:
                            t_kelvin = val
                            source_file = "0/T"
                            break
                    if t_kelvin is not None:
                        break
            if t_kelvin is None and t_int is not None:
                t_kelvin = t_int
                source_file = "0/T (internalField)"

        # Fallback to 0/p if T is defined there as a property
        if t_kelvin is None and p_file:
            _, p_bounds = self._parse_foam_dict(p_file, field_type="T")
            for name, val in p_bounds.items():
                if val is not None:
                    t_kelvin = val
                    source_file = f"0/p ({name})"
                    break

        if t_kelvin is not None:
            # 293.0 K corresponds to 20.0 °C (CfdOF rounds 293.15 K down)
            if abs(t_kelvin - 293.0) < 0.05 or abs(t_kelvin - 293.15) < 0.05:
                t_celsius = 20.0
            else:
                t_celsius = t_kelvin - 273.15
            self.temp_c_var.set(f"{t_celsius:.1f}")
            updates.append(f"Temperature: {t_celsius:.1f} °C ({t_kelvin:.2f} K from {source_file})")

        if updates:
            self.log("=" * 64)
            self.log("AUTOMATICALLY LOADED FROM CASE FILES (0/):")
            for u in updates:
                self.log(f"  ✓ {u}")
            self.log("=" * 64)

    def write_setup_files(self):
        """Generates inletFlow, planeFlow, and centerProbe in system/."""
        case_dir = self.case_path_var.get().strip()
        if not case_dir:
            messagebox.showerror("Error", "Please select a case directory first.")
            return False

        system_dir = os.path.join(case_dir, "system")

        if not os.path.exists(system_dir):
            messagebox.showerror("Error", f"Directory 'system' does not exist in:\n{case_dir}")
            return False

        self._save_config()

        try:
            z_mm = float(self.plane_z_var.get().replace(",", "."))
            z_m = z_mm / 1000.0
        except ValueError:
            messagebox.showerror("Error", "Invalid value for plane Z-distance [mm]!")
            return False

        inlet_name = self.inlet_name_var.get().strip()
        normal_vec = self.normal_var.get().strip()

        content_inlet = (
            "type            surfaceFieldValue;\n"
            'libs            ("libfieldFunctionObjects.so");\n'
            "writeControl    always;\n"
            "writeFields     false;\n"
            "log             false;\n"
            "regionType      patch;\n"
            f"name            {inlet_name};\n"
            "operation       areaNormalIntegrate;\n"
            "fields          (U);\n"
        )

        content_plane = (
            "type            surfaceFieldValue;\n"
            'libs            ("libfieldFunctionObjects.so");\n'
            "writeControl    always;\n"
            "writeFields     false;\n"
            "log             false;\n"
            "regionType      sampledSurface;\n"
            "name            planeCut;\n"
            "sampledSurfaceDict\n"
            "{\n"
            "    type        cuttingPlane;\n"
            "    planeType   pointAndNormal;\n"
            "    pointAndNormalDict\n"
            "    {\n"
            f"        point   (0 0 {z_m:.6f});\n"
            f"        normal  ({normal_vec});\n"
            "    }\n"
            "    interpolate true;\n"
            "}\n"
            "operation       areaNormalIntegrate;\n"
            "fields          (U);\n"
        )

        content_probe = (
            "type            probes;\n"
            'libs            ("libsampling.so");\n'
            "writeControl    always;\n"
            "writeFields     false;\n"
            "probeLocations\n"
            "(\n"
            f"    (0 0 {z_m:.6f})\n"
            ");\n"
            "fields          (p U);\n"
        )

        try:
            with open(os.path.join(system_dir, "inletFlow"), "w") as f:
                f.write(content_inlet)
            with open(os.path.join(system_dir, "planeFlow"), "w") as f:
                f.write(content_plane)
            with open(os.path.join(system_dir, "centerProbe"), "w") as f:
                f.write(content_probe)

            self.log("=" * 64)
            self.log(f"Setup files updated in {system_dir}:")
            self.log("  -> inletFlow, planeFlow, centerProbe")
            return True
        except Exception as e:
            messagebox.showerror("Error Writing Files", str(e))
            return False

    def start_evaluation_thread(self):
        threading.Thread(target=self._run_evaluation, daemon=True).start()

    def _run_evaluation(self):
        self.btn_run_eval.config(state="disabled")

        if not self.write_setup_files():
            self.btn_run_eval.config(state="normal")
            return

        case_dir = self.case_path_var.get().strip()
        docker_img = self.docker_img_var.get().strip()

        try:
            p_gauge = float(self.p_gauge_var.get().replace(",", "."))
            p_amb = float(self.p_amb_var.get().replace(",", "."))
            temp_k = float(self.temp_c_var.get().replace(",", ".")) + 273.15
        except ValueError:
            messagebox.showerror("Error", "Invalid value for pressure or temperature!")
            self.btn_run_eval.config(state="normal")
            return

        R_spec = 287.058
        p_inlet_abs = (p_gauge + p_amb) * 1e5
        p_amb_abs = p_amb * 1e5

        rho_inlet = p_inlet_abs / (R_spec * temp_k)
        rho_ambient = p_amb_abs / (R_spec * temp_k)

        self.log(f"Calculated gas densities (T = {temp_k - 273.15:.1f} °C):")
        self.log(f"  -> Inlet ({p_inlet_abs/1e5:.3f} bar abs): rho = {rho_inlet:.4f} kg/m³")
        self.log(f"  -> Ambient ({p_amb_abs/1e5:.3f} bar abs): rho = {rho_ambient:.4f} kg/m³")
        self.log("Starting reconstruction and analysis in Docker container...")

        docker_bin = (
            shutil.which("docker")
            or ("/usr/local/bin/docker" if os.path.exists("/usr/local/bin/docker") else None)
            or ("/opt/homebrew/bin/docker" if os.path.exists("/opt/homebrew/bin/docker") else None)
            or (os.path.expanduser("~/.docker/bin/docker") if os.path.exists(os.path.expanduser("~/.docker/bin/docker")) else None)
            or "/Applications/Docker.app/Contents/Resources/bin/docker"
        )
        if not shutil.which(docker_bin) and not os.path.exists(docker_bin):
            self.log("ERROR: 'docker' command was not found on the system!")
            self.log("Please ensure Docker Desktop is installed and running.")
            messagebox.showerror("Docker Error", "Docker was not found. Please ensure Docker Desktop is installed and running.")
            return

        docker_cmd = (
            f'"{docker_bin}" run --platform linux/amd64 --rm '
            f'-v "{case_dir}":/home/openfoam/run '
            f'-w /home/openfoam/run '
            f'{docker_img} '
            f'/bin/bash -lc "cd /home/openfoam/run && '
            f'rm -rf postProcessing/inletFlow postProcessing/planeFlow postProcessing/centerProbe && '
            f'reconstructPar -case /home/openfoam/run -latestTime && '
            f'postProcess -case /home/openfoam/run -func inletFlow -latestTime && '
            f'postProcess -case /home/openfoam/run -func planeFlow -latestTime && '
            f'postProcess -case /home/openfoam/run -func centerProbe -latestTime"'
        )

        stdout_output = ""
        try:
            proc = subprocess.run(docker_cmd, shell=True, env=os.environ, capture_output=True, text=True)
            stdout_output = proc.stdout
            if proc.returncode != 0:
                self.log("ERROR during Docker execution:")
                self.log(proc.stderr)
                messagebox.showerror("Error", "Analysis failed. Details in protocol.")
                return
        except Exception as e:
            self.log(f"Error: {e}")
            return
        finally:
            self.btn_run_eval.config(state="normal")

        v_inlet = self._extract_flow_from_stdout(stdout_output, "inletFlow")
        v_plane = self._extract_flow_from_stdout(stdout_output, "planeFlow")

        if v_inlet is None:
            v_inlet = self._read_surface_val(case_dir, "inletFlow")
        if v_plane is None:
            v_plane = self._read_surface_val(case_dir, "planeFlow")

        rho_iso = 1.2041
        rho_din = 1.2930

        self.log("\nFLOW ANALYSIS RESULTS:")
        self.log("-" * 64)
        self.log(f"{'Position':<16} | {'Mass [kg/s]':<13} | {'ISO [Nl/min]':<12} | {'DIN [Nl/min]'}")
        self.log("-" * 64)

        m_in = 0
        if v_inlet is not None:
            m_in = v_inlet * rho_inlet
            nl_iso_in = (m_in * 60 / rho_iso) * 1000
            nl_din_in = (m_in * 60 / rho_din) * 1000
            self.log(f"{'Inlet':<16} | {m_in:<13.6f} | {nl_iso_in:<12.1f} | {nl_din_in:.1f}")
        else:
            self.log(f"{'Inlet':<16} | No data found")

        z_label = f"Plane z={self.plane_z_var.get()}mm"
        if v_plane is not None:
            m_pl = v_plane * rho_ambient
            nl_iso_pl = (m_pl * 60 / rho_iso) * 1000
            nl_din_pl = (m_pl * 60 / rho_din) * 1000
            self.log(f"{z_label:<16} | {m_pl:<13.6f} | {nl_iso_pl:<12.1f} | {nl_din_pl:.1f}")
            if v_inlet is not None and m_in > 0:
                amplification = m_pl / m_in
                self.log("-" * 64)
                self.log(f"Flow amplification factor: {amplification:.2f}x (plane flow / inlet flow)")
        else:
            self.log(f"{z_label:<16} | No data found")

        p_val = self._read_probe_scalar(case_dir, "p")
        u_val = self._read_probe_vector(case_dir, "U")

        self.log("\nPOINT MEASUREMENT AT CENTER (0, 0, " + self.plane_z_var.get() + " mm):")
        self.log("-" * 64)

        if u_val is not None:
            ux, uy, uz, u_mag = u_val
            kmh = u_mag * 3.6
            self.log(f"Velocity |U|: {u_mag:.2f} m/s ({kmh:.1f} km/h)")
            self.log(f"Vector (Ux, Uy, Uz): ({ux:.2f}, {uy:.2f}, {uz:.2f}) m/s")
        else:
            self.log("Velocity: No probe data found")

        if p_val is not None:
            p_bar = p_val / 100000.0
            self.log(f"Static pressure p: {p_val:.1f} Pa  ({p_bar:.4f} bar abs)")
        else:
            self.log("Pressure: No probe data found")

        amplification_val = None
        if v_inlet is not None and m_in > 0 and v_plane is not None:
            amplification_val = m_pl / m_in

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        case_name = self._get_case_name(case_dir)

        ux_val = f"{u_val[0]:.2f}" if u_val is not None else ""
        uy_val = f"{u_val[1]:.2f}" if u_val is not None else ""
        uz_val = f"{u_val[2]:.2f}" if u_val is not None else ""
        umag_val = f"{u_val[3]:.2f}" if u_val is not None else ""
        kmh_val = f"{u_val[3] * 3.6:.1f}" if u_val is not None else ""

        p_pa_val = f"{p_val:.1f}" if p_val is not None else ""
        p_bar_val = f"{p_val / 1e5:.4f}" if p_val is not None else ""

        self.last_results = {
            "Case Name": case_name,
            "Timestamp": now_str,
            "Case Directory": case_dir,
            "Inlet Patch": self.inlet_name_var.get().strip(),
            "Open Patch": self.open_name_var.get().strip(),
            "Plane Z [mm]": self.plane_z_var.get().strip(),
            "Inlet Gauge Pressure [bar g]": f"{p_gauge:.3f}",
            "Ambient Pressure [bar abs]": f"{p_amb:.4f}",
            "Inlet Abs Pressure [bar abs]": f"{p_inlet_abs/1e5:.4f}",
            "Temperature [C]": f"{temp_k - 273.15:.1f}",
            "Inlet Density [kg/m3]": f"{rho_inlet:.4f}",
            "Ambient Density [kg/m3]": f"{rho_ambient:.4f}",
            "Inlet Volume Flow [m3/s]": f"{v_inlet:.6f}" if v_inlet is not None else "",
            "Inlet Mass Flow [kg/s]": f"{m_in:.6f}" if v_inlet is not None else "",
            "Inlet Flow ISO [Nl/min]": f"{nl_iso_in:.1f}" if v_inlet is not None else "",
            "Inlet Flow DIN [Nl/min]": f"{nl_din_in:.1f}" if v_inlet is not None else "",
            "Plane Volume Flow [m3/s]": f"{v_plane:.6f}" if v_plane is not None else "",
            "Plane Mass Flow [kg/s]": f"{m_pl:.6f}" if v_plane is not None else "",
            "Plane Flow ISO [Nl/min]": f"{nl_iso_pl:.1f}" if v_plane is not None else "",
            "Plane Flow DIN [Nl/min]": f"{nl_din_pl:.1f}" if v_plane is not None else "",
            "Flow Amplification Factor": f"{amplification_val:.2f}" if amplification_val is not None else "",
            "Center Probe |U| [m/s]": umag_val,
            "Center Probe Velocity [km/h]": kmh_val,
            "Center Probe Ux [m/s]": ux_val,
            "Center Probe Uy [m/s]": uy_val,
            "Center Probe Uz [m/s]": uz_val,
            "Center Probe Static Pressure [Pa]": p_pa_val,
            "Center Probe Static Pressure [bar abs]": p_bar_val,
        }

        self.log("=" * 64)
        self.log(f"Ready. Click 'Export to CSV' to append results for case '{case_name}'.")
        self.log("=" * 64)

    def _extract_flow_from_stdout(self, stdout_text, func_name):
        pattern = rf"surfaceFieldValue {func_name} write:.*?areaNormalIntegrate\([^)]+\) of U = ([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        match = re.search(pattern, stdout_text, re.DOTALL)
        if match:
            return abs(float(match.group(1)))
        return None

    def _read_surface_val(self, case_dir, func_name):
        pattern = os.path.join(case_dir, "postProcessing", func_name, "**", "surfaceFieldValue*.dat")
        files = glob.glob(pattern, recursive=True)
        if not files:
            return None
        latest_file = max(files, key=os.path.getmtime)
        with open(latest_file, "r") as f:
            lines = [line.strip() for line in f if not line.startswith("#") and line.strip()]
            if not lines:
                return None
            return abs(float(lines[-1].split()[-1]))

    def _read_probe_scalar(self, case_dir, field_name):
        pattern = os.path.join(case_dir, "postProcessing", "centerProbe", "**", field_name)
        files = glob.glob(pattern, recursive=True)
        if not files:
            return None
        latest_file = max(files, key=os.path.getmtime)
        with open(latest_file, "r") as f:
            lines = [line.strip() for line in f if not line.startswith("#") and line.strip()]
            if not lines:
                return None
            return float(lines[-1].split()[-1])

    def _read_probe_vector(self, case_dir, field_name):
        pattern = os.path.join(case_dir, "postProcessing", "centerProbe", "**", field_name)
        files = glob.glob(pattern, recursive=True)
        if not files:
            return None
        latest_file = max(files, key=os.path.getmtime)
        with open(latest_file, "r") as f:
            lines = [line.strip() for line in f if not line.startswith("#") and line.strip()]
            if not lines:
                return None
            match = re.search(r'\((.*?)\)', lines[-1])
            if match:
                parts = match.group(1).split()
                ux, uy, uz = float(parts[0]), float(parts[1]), float(parts[2])
                u_mag = math.sqrt(ux**2 + uy**2 + uz**2)
                return ux, uy, uz, u_mag
            return None

if __name__ == "__main__":
    root = tk.Tk()
    app = CFDFlowGUI(root)
    root.mainloop()
