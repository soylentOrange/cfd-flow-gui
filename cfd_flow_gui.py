import os
import glob
import re
import math
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

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
        self.root.title("OpenFOAM Durchfluss- & Punkt-Analyse (CfdOF)")
        self.root.geometry("780x740")
        self.root.minsize(720, 580)

        # Variablen initialisieren
        self.case_path_var = tk.StringVar(value="/Users/wendlandt-r/CfdOF/Unnamed/case")
        self.inlet_name_var = tk.StringVar(value="inlet")
        self.plane_z_var = tk.StringVar(value="75")
        self.normal_var = tk.StringVar(value="0 0 1")
        self.p_gauge_var = tk.StringVar(value="2.0")
        self.p_amb_var = tk.StringVar(value="1.0")
        self.temp_c_var = tk.StringVar(value="20.0")
        self.docker_img_var = tk.StringVar(value="cfdof-openfoam:opencfd-mac")

        self._build_ui()
        self.auto_load_case_parameters(silent=True)

    def _build_ui(self):
        # 1. Fallverzeichnis
        frame_dir = ttk.LabelFrame(self.root, text=" 1. Fallverzeichnis (Case) ", padding=10)
        frame_dir.pack(fill="x", padx=10, pady=5)

        entry_dir = ttk.Entry(frame_dir, textvariable=self.case_path_var)
        entry_dir.pack(side="left", fill="x", expand=True, padx=(0, 5))

        btn_browse = ttk.Button(frame_dir, text="Durchsuchen...", command=self._browse_dir)
        btn_browse.pack(side="right")

        # 2. Parameter
        frame_params = ttk.LabelFrame(self.root, text=" 2. Geometrie- & Druckeinstellungen ", padding=10)
        frame_params.pack(fill="x", padx=10, pady=5)

        # Zeile 0: Patch & Ebene
        ttk.Label(frame_params, text="Inlet-Patch Name:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frame_params, textvariable=self.inlet_name_var, width=15).grid(row=0, column=1, sticky="w", padx=5)

        ttk.Label(frame_params, text="Ebene Z-Abstand [mm]:").grid(row=0, column=2, sticky="w", padx=(20, 0), pady=3)
        ttk.Entry(frame_params, textvariable=self.plane_z_var, width=10).grid(row=0, column=3, sticky="w", padx=5)

        # Zeile 1: Druckparameter
        ttk.Label(frame_params, text="Einlass-Überdruck [bar ü]:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame_params, textvariable=self.p_gauge_var, width=15).grid(row=1, column=1, sticky="w", padx=5)

        ttk.Label(frame_params, text="Umgebungsdruck [bar abs]:").grid(row=1, column=2, sticky="w", padx=(20, 0), pady=3)
        ttk.Entry(frame_params, textvariable=self.p_amb_var, width=10).grid(row=1, column=3, sticky="w", padx=5)

        # Zeile 2: Temperatur & Docker
        ttk.Label(frame_params, text="Temperatur [°C]:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frame_params, textvariable=self.temp_c_var, width=15).grid(row=2, column=1, sticky="w", padx=5)

        ttk.Label(frame_params, text="Docker-Image:").grid(row=2, column=2, sticky="w", padx=(20, 0), pady=3)
        ttk.Entry(frame_params, textvariable=self.docker_img_var, width=22).grid(row=2, column=3, sticky="w", padx=5)

        # Zeile 3: Auto-Erkennungs-Button
        btn_autodetect = ttk.Button(
            frame_params, 
            text="🔄 Werte aus Case (0/p, 0/T) neu einlesen", 
            command=self.auto_load_case_parameters
        )
        btn_autodetect.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 2))

        # 3. Zentraler Aktions-Knopf
        frame_actions = ttk.Frame(self.root, padding=5)
        frame_actions.pack(fill="x", padx=10, pady=5)

        self.btn_run_eval = ttk.Button(
            frame_actions, 
            text="⚡ Setup erstellen & Auswertung starten (Docker)", 
            command=self.start_evaluation_thread
        )
        self.btn_run_eval.pack(fill="x", ipady=5)

        # 4. Status und Ausgabebereich
        frame_out = ttk.LabelFrame(self.root, text=" Auswertung & Protokoll ", padding=10)
        frame_out.pack(fill="both", expand=True, padx=10, pady=5)

        self.txt_output = ScrolledText(frame_out, wrap="word", font=("Courier", 11))
        self.txt_output.pack(fill="both", expand=True)

        self.log("Bereit. Wähle das Case-Verzeichnis aus.")

    def log(self, text):
        self.txt_output.insert("end", text + "\n")
        self.txt_output.see("end")

    def _browse_dir(self):
        folder = filedialog.askdirectory(initialdir=self.case_path_var.get())
        if folder:
            self.case_path_var.set(folder)
            self.auto_load_case_parameters()

    def _parse_foam_dict(self, file_path, field_type="p"):
        """Parst OpenFOAM-Dictionaries getrennt nach Druck ('p') oder Temperatur ('T')."""
        if not os.path.exists(file_path):
            return None, {}
        try:
            with open(file_path, "r", errors="ignore") as f:
                content = f.read()

            content = re.sub(r'//.*', '', content)
            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

            # internalField (nur explizite Zahlenwerte erfassen)
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

                    # Feld-spezifische Suchmuster
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
        """Liest p und T automatisch aus den Case-Dateien ein."""
        case_dir = self.case_path_var.get().strip()
        inlet_name = self.inlet_name_var.get().strip().lower()

        p_paths = [os.path.join(case_dir, "0", "p"), os.path.join(case_dir, "0.orig", "p")]
        t_paths = [os.path.join(case_dir, "0", "T"), os.path.join(case_dir, "0.orig", "T")]

        p_file = next((p for p in p_paths if os.path.exists(p)), None)
        t_file = next((p for p in t_paths if os.path.exists(p)), None)

        if not p_file and not t_file:
            if not silent:
                messagebox.showwarning("Hinweis", "Weder 0/p noch 0/T im Case-Ordner gefunden.")
            return

        updates = []

        # 1. Drücke ermitteln (aus 0/p)
        if p_file:
            p_int, p_bounds = self._parse_foam_dict(p_file, field_type="p")

            p_inlet_pa = None
            for name, val in p_bounds.items():
                if name.lower() == inlet_name and val is not None:
                    p_inlet_pa = val
                    break

            p_amb_pa = None
            found_amb_name = None
            for candidate in ["open", "outlet", "atmosphere", "ambient", "farfield"]:
                for name, val in p_bounds.items():
                    if candidate in name.lower() and val is not None:
                        p_amb_pa = val
                        found_amb_name = name
                        break
                if p_amb_pa is not None:
                    break

            if p_amb_pa is not None:
                p_amb_bar = p_amb_pa / 1e5
                self.p_amb_var.set(f"{p_amb_bar:.5g}")
                updates.append(f"Umgebungsdruck: {p_amb_bar:.4f} bar abs (aus Patch '{found_amb_name}')")
            elif p_int is not None:
                p_amb_bar = p_int / 1e5
                self.p_amb_var.set(f"{p_amb_bar:.5g}")
                updates.append(f"Umgebungsdruck: {p_amb_bar:.4f} bar abs (aus internalField)")

            if p_inlet_pa is not None and p_amb_pa is not None:
                p_gauge_bar = (p_inlet_pa - p_amb_pa) / 1e5
                self.p_gauge_var.set(f"{p_gauge_bar:.3f}")
                updates.append(f"Einlass-Überdruck: {p_gauge_bar:.3f} bar ü (Differenz '{inlet_name}' - '{found_amb_name}')")

        # 2. Temperatur ermitteln (primär aus 0/T, Fallback auf 0/p)
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
                for candidate in ["open", "outlet", "atmosphere", "ambient"]:
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

        # Fallback auf 0/p, falls T dort als Eigenschaft definiert ist
        if t_kelvin is None and p_file:
            _, p_bounds = self._parse_foam_dict(p_file, field_type="T")
            for name, val in p_bounds.items():
                if val is not None:
                    t_kelvin = val
                    source_file = f"0/p ({name})"
                    break

        if t_kelvin is not None:
            # 293.0 K entspricht 20.0 °C (CfdOF rundet 293.15 K ab)
            if abs(t_kelvin - 293.0) < 0.05 or abs(t_kelvin - 293.15) < 0.05:
                t_celsius = 20.0
            else:
                t_celsius = t_kelvin - 273.15
            self.temp_c_var.set(f"{t_celsius:.1f}")
            updates.append(f"Temperatur: {t_celsius:.1f} °C ({t_kelvin:.2f} K aus {source_file})")

        if updates:
            self.log("=" * 64)
            self.log("AUTOMATISCH AUS CASE-DATEIEN (0/) EINGELESEN:")
            for u in updates:
                self.log(f"  ✓ {u}")
            self.log("=" * 64)

    def write_setup_files(self):
        """Erzeugt inletFlow, planeFlow und centerProbe in system/."""
        case_dir = self.case_path_var.get().strip()
        system_dir = os.path.join(case_dir, "system")

        if not os.path.exists(system_dir):
            messagebox.showerror("Fehler", f"Verzeichnis 'system' existiert nicht in:\n{case_dir}")
            return False

        try:
            z_mm = float(self.plane_z_var.get().replace(",", "."))
            z_m = z_mm / 1000.0
        except ValueError:
            messagebox.showerror("Fehler", "Ungültiger Wert für Z-Abstand [mm]!")
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
            self.log(f"Setup-Dateien in {system_dir} aktualisiert:")
            self.log("  -> inletFlow, planeFlow, centerProbe")
            return True
        except Exception as e:
            messagebox.showerror("Fehler beim Schreiben", str(e))
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
            messagebox.showerror("Fehler", "Ungültige Zahl bei Druck oder Temperatur!")
            self.btn_run_eval.config(state="normal")
            return

        R_spec = 287.058
        p_inlet_abs = (p_gauge + p_amb) * 1e5
        p_amb_abs = p_amb * 1e5

        rho_inlet = p_inlet_abs / (R_spec * temp_k)
        rho_ambient = p_amb_abs / (R_spec * temp_k)

        self.log(f"Berechnete Gasdichten (T = {temp_k - 273.15:.1f} °C):")
        self.log(f"  -> Einlass ({p_inlet_abs/1e5:.3f} bar abs): rho = {rho_inlet:.4f} kg/m³")
        self.log(f"  -> Umgebung ({p_amb_abs/1e5:.3f} bar abs): rho = {rho_ambient:.4f} kg/m³")
        self.log("Starte Rekonstruktion und Auswertung im Docker-Container...")

        docker_bin = (
            shutil.which("docker")
            or ("/usr/local/bin/docker" if os.path.exists("/usr/local/bin/docker") else None)
            or ("/opt/homebrew/bin/docker" if os.path.exists("/opt/homebrew/bin/docker") else None)
            or (os.path.expanduser("~/.docker/bin/docker") if os.path.exists(os.path.expanduser("~/.docker/bin/docker")) else None)
            or "/Applications/Docker.app/Contents/Resources/bin/docker"
        )
        if not shutil.which(docker_bin) and not os.path.exists(docker_bin):
            self.log("FEHLER: 'docker' Befehl wurde auf dem System nicht gefunden!")
            self.log("Bitte sicherstellen, dass Docker Desktop installiert ist und läuft.")
            messagebox.showerror("Docker Fehler", "Docker wurde nicht gefunden. Bitte sicherstellen, dass Docker Desktop installiert ist.")
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
                self.log("FEHLER bei Docker-Ausführung:")
                self.log(proc.stderr)
                messagebox.showerror("Fehler", "Auswertung fehlgeschlagen. Details im Protokoll.")
                return
        except Exception as e:
            self.log(f"Fehler: {e}")
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

        self.log("\nERGEBNISSE DER DURCHFLUSS-ANALYSE:")
        self.log("-" * 64)
        self.log(f"{'Position':<16} | {'Masse [kg/s]':<13} | {'ISO [Nl/min]':<12} | {'DIN [Nl/min]'}")
        self.log("-" * 64)

        m_in = 0
        if v_inlet is not None:
            m_in = v_inlet * rho_inlet
            nl_iso_in = (m_in * 60 / rho_iso) * 1000
            nl_din_in = (m_in * 60 / rho_din) * 1000
            self.log(f"{'Inlet':<16} | {m_in:<13.6f} | {nl_iso_in:<12.1f} | {nl_din_in:.1f}")
        else:
            self.log(f"{'Inlet':<16} | Keine Daten gefunden")

        z_label = f"Ebene z={self.plane_z_var.get()}mm"
        if v_plane is not None:
            m_pl = v_plane * rho_ambient
            nl_iso_pl = (m_pl * 60 / rho_iso) * 1000
            nl_din_pl = (m_pl * 60 / rho_din) * 1000
            self.log(f"{z_label:<16} | {m_pl:<13.6f} | {nl_iso_pl:<12.1f} | {nl_din_pl:.1f}")
            if v_inlet is not None and m_in > 0:
                entrainment = m_pl / m_in
                self.log("-" * 64)
                self.log(f"Ejektor-Verstärkungsfaktor: {entrainment:.2f}x (Mischstrom / Treibstrahl)")
        else:
            self.log(f"{z_label:<16} | Keine Daten gefunden")

        p_val = self._read_probe_scalar(case_dir, "p")
        u_val = self._read_probe_vector(case_dir, "U")

        self.log("\nPUNKTMESSUNG IM ZENTRUM (0, 0, " + self.plane_z_var.get() + " mm):")
        self.log("-" * 64)

        if u_val is not None:
            ux, uy, uz, u_mag = u_val
            kmh = u_mag * 3.6
            self.log(f"Geschwindigkeit |U|: {u_mag:.2f} m/s ({kmh:.1f} km/h)")
            self.log(f"Vektor (Ux, Uy, Uz): ({ux:.2f}, {uy:.2f}, {uz:.2f}) m/s")
        else:
            self.log("Geschwindigkeit: Keine Probe-Daten gefunden")

        if p_val is not None:
            p_bar = p_val / 100000.0
            self.log(f"Statischer Druck p : {p_val:.1f} Pa  ({p_bar:.4f} bar abs)")
        else:
            self.log("Druck: Keine Probe-Daten gefunden")

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
