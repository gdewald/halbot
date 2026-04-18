# Project Restructure Plan

* Decouple the tray and daemon
    * Migrate tray and daemon communication to IPC using gRPC
        * New folder for protobuf definitions (gRPC)
    * Migrate deamon runtime to NSSM
        * Tray uses NSSM for daemon process management, IPC for other control functions
        * Tray logger tails logs from file as before
* Productionize the build
    * pyinstaller to package the tray application
    * NSSM for running and managing the daemon process


New folder structure (roughly):

```
halbot/
├── proto/
│   └── mgmt.proto
├── gen/
│   ├── mgmt_pb2.py
│   └── mgmt_pb2_grpc.py
├── halbot/
│   ├── __init__.py
│   ├── mgmt_server.py        # gRPC server, runs in daemon
│   ├── bot.py                # main discord bot logic
│   └── daemon.py             # entrypoint: starts service + gRPC server
├── tray/
│   ├── __init__.py
│   ├── mgmt_client.py        # gRPC client, used by management/tray GUI
│   └── tray.py               # entrypoint: launches tray / GUI
├── build_daemon.spec    # PyInstaller spec for daemon exe
├── build_tray.spec      # PyInstaller spec for GUI exe
├── scripts/
│   └── gen_proto.ps1    # wraps the protoc codegen command
└── pyproject.toml
```
