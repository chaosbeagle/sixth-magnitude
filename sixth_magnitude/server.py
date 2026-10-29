"""Loopback browser surface and compact JSON actions for a Codex visual worker."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from . import schema, telemetry, __version__
from .exports import export_run
from .imaging import Images
from .store import Conflict, Store, read
from .workbench import Workbench

ROOT=Path(__file__).resolve().parent
UI=ROOT/"ui"
VENDOR=UI/"vendor"


class Server(HTTPServer):
    def __init__(self, directory, port):
        self.store=Store(directory)
        self.images=Images(self.store)
        self.workbench=Workbench(self.store, self.images) if self.store.profile == "lung-pathology" else None
        super().__init__(("127.0.0.1",port),Handler)

    def server_close(self):
        self.images.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/tiles/","/evidence/")):
            super().log_message(fmt,*args)

    def trusted(self):
        allowed=f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host")!=allowed:
            raise PermissionError("Use the advertised loopback URL")
        origin=self.headers.get("Origin")
        if origin and origin!=f"http://{allowed}":
            raise PermissionError("Unexpected origin")

    def send(self, data, mime="application/json; charset=utf-8", status=200):
        if not isinstance(data,bytes):
            data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","private, max-age=3600" if self.path.startswith(("/tiles/","/evidence/")) else "no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass

    def do_GET(self):
        try:
            self.trusted()
            path=urlsplit(self.path).path
            parts=path.strip("/").split("/")
            store=self.server.store
            if path=="/":
                return self.send((UI/("agent.html" if store.profile == "lung-pathology" else "inspect.html")).read_bytes(),"text/html; charset=utf-8")
            if path=="/review":
                return self.send((UI/("index.html" if store.profile == "lung-pathology" else "inspect.html")).read_bytes(),"text/html; charset=utf-8")
            if path in ("/app.js","/app.css","/agent.js","/agent.css","/inspect.js","/inspect.css"):
                return self.send((UI/path[1:]).read_bytes(),"application/javascript" if path.endswith("js") else "text/css")
            if path in ("/vendor/openseadragon.min.js","/vendor/annotorious.js","/vendor/annotorious.css"):
                return self.send((VENDOR/parts[-1]).read_bytes(),"text/css" if path.endswith("css") else "application/javascript")
            if path=="/api/run":
                return self.send({"run_id":store.manifest["run_id"],"workspace_id":store.manifest.get("workspace_id"),"purpose":store.manifest["purpose"],
                    "task_mode":store.manifest["task_mode"],"model_requested":store.manifest["model_requested"],
                    "effort_requested":store.manifest["effort_requested"],"queue":store.queue(),"profile":store.profile,"schema_version":store.schema.VERSION})
            if path=="/api/schema":
                return self.send({"schema":store.schema.SCHEMA,"defaults":store.schema.defaults(),"labels":store.schema.LABELS})
            if path=="/api/usage":
                return self.send(telemetry.report(store))
            if len(parts)==3 and parts[:2]==["api","case"]:
                case=parts[2]
                self.server.images.reader(case)
                source={k:v for k,v in store.source(case).items() if k!="source"}
                return self.send({"source":source,"state":store.state(case),"planned_rois":store.cases[case].get("planned_rois",[])})
            if len(parts)==3 and parts[:2]==["api","evidence"]:
                from .store import identifier
                evidence=read(store.root/"evidence"/identifier(parts[2])/"metadata.json")
                if evidence.get('kind') == 'work_packet_region':
                    self.server.workbench.load_packet(evidence['packet_id'], evidence['case_id'])
                    detail=self.server.images.prepare(evidence['case_id'], evidence['geometry'])
                    if detail['evidence_id'] != evidence['native_detail_evidence_id']:
                        raise ValueError('Native detail identity mismatch')
                    evidence={**detail, 'render_evidence_id': detail['evidence_id'], 'evidence_id': evidence['evidence_id']}
                return self.send(evidence)
            if len(parts)==3 and parts[:2]==['api','brief']:
                if self.server.workbench is None:
                    return self.send({"case_id":parts[2],"state":store.state(parts[2]),"profile":store.profile})
                return self.send(self.server.workbench.brief(parts[2]))
            if len(parts)==3 and parts[0]=='work-packet':
                if self.server.workbench is None: raise ValueError("Work packets require the pathology profile")
                packet=self.server.workbench.load_packet(parts[1])
                if parts[2] not in packet['files']:
                    raise ValueError('Unknown packet file')
                return self.send((store.root/'work_packets'/parts[1]/parts[2]).read_bytes(),'image/png')
            if len(parts)==7 and parts[0]=="tiles":
                _,case,key,level,x,y,filename=parts
                if filename!="tile.png":
                    raise ValueError("Invalid tile filename")
                return self.send(self.server.images.tile(case,key,int(level),int(x),int(y)),"image/png")
            if len(parts)==3 and parts[0]=="evidence":
                from .store import identifier
                directory=store.root/"evidence"/identifier(parts[1])
                meta=read(directory/"metadata.json")
                if parts[2] not in {v["file"] for v in meta["files"].values()}:
                    raise ValueError("Unknown evidence file")
                return self.send((directory/parts[2]).read_bytes(),"image/png")
            if path=="/schema.json":
                return self.send(store.schema.SCHEMA)
            if path=="/api/health":
                return self.send({"status":"ok","framework":"六等星","version":__version__,"observation_schema_version":store.schema.VERSION})
            self.send({"error":"Not found"},status=404)
        except PermissionError as exc:
            self.send({"error":str(exc)},status=403)
        except (ValueError,KeyError,OSError,TypeError) as exc:
            self.send({"error":str(exc)},status=400)

    def do_POST(self):
        try:
            self.trusted()
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=2_000_000:
                raise ValueError("Invalid payload size")
            data=json.loads(self.rfile.read(size),parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            store=self.server.store
            if self.path in ('/api/packet', '/api/territories'):
                if self.server.workbench is None: raise ValueError('Territories require the pathology profile')
                case=data['case_id']
                if data['source_key'] != store.source(case)['source_key']:
                    raise Conflict('Source mismatch')
                self.server.images.reader(case)
                if self.path == '/api/packet':
                    return self.send(self.server.workbench.packet(case, data.get('bbox_level0')))
                actor=data.pop('actor'); data.pop('case_id')
                if data.get('action') != 'batch_save':
                    raise ValueError('Territory endpoint requires batch_save')
                return self.send(self.server.workbench.submit(case, data, actor))
            if self.path=="/api/prepare":
                case=data["case_id"]
                if data["source_key"]!=store.source(case)["source_key"]:
                    raise Conflict("Source mismatch")
                return self.send(self.server.images.prepare(case,data["geometry"]))
            if self.path=="/api/action":
                case=data.pop("case_id")
                actor=data.pop("actor")
                # Also verify the current source for mutation-only clients.
                self.server.images.reader(case)
                if data['action'] == 'batch_visual':
                    for item in data['items']:
                        if item.get('view_packet_id'):
                            if self.server.workbench is None: raise ValueError('Work packets require the pathology profile')
                            packet=self.server.workbench.load_packet(item['view_packet_id'], case)
                            g=store.state(case)['annotations'][item['annotation_id']]['geometry']['bbox_level0']
                            p=packet['bbox_level0']
                            if not (g[0]>=p[0]-.001 and g[1]>=p[1]-.001 and g[2]<=p[2]+.001 and g[3]<=p[3]+.001):
                                raise ValueError('Visual confirmation requires the entire region in the inspected packet')
                if data["action"]=="batch_save":
                    items=data.get("items", [])
                    if not isinstance(items,list) or not 1 <= len(items) <= 64: raise ValueError("Batch requires 1-64 regions")
                    prepared=[(r,self.server.images.prepare(case,r["geometry"])) for r in items]
                    result=store.batch_save(case,data,actor,prepared)
                elif data["action"]=="save":
                    evidence=self.server.images.prepare(case,data["geometry"])
                    result=store.save(case,data,actor,evidence)
                else:
                    result=store.action(case,data,actor)
                return self.send(result)
            if self.path=="/api/export":
                return self.send(export_run(store))
            self.send({"error":"Not found"},status=404)
        except PermissionError as exc:
            self.send({"error":str(exc)},status=403)
        except Conflict as exc:
            self.send({"error":str(exc),"recover":"reload"},status=409)
        except (ValueError,KeyError,OSError,TypeError) as exc:
            self.send({"error":str(exc)},status=400)


def serve(root, port):
    with Server(root,port) as server:
        print(f"Sesta Workbench http://127.0.0.1:{server.server_port}/",flush=True)
        print(f"Run: {server.store.root}",flush=True)
        server.serve_forever()
