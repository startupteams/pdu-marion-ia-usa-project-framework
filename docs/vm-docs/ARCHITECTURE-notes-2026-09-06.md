# MIAM PDU Control Architecture Notes

Revision: 2026-09-06
Source: `2026-09-06 Server Architecture_ what Ethernet port and what power port are connected to what asset.xlsx` plus the current OPNsense screenshot.

## Control plane

- **MIAM-00133** — Proxmox node / Tailscale routing host — `10.0.20.133` — MAC `20:88:10:df:5f:29`.
  - Hosts **VM 154 (`pdu-control`)** at `10.0.20.154`.
  - Powered by **MIAM-00153 outlet 9**.
  - **OFF / REBOOT must remain protected** in the PDU dashboard because turning it off removes the host that executes PDU SSH commands.
- **VM 154** — `pdu-control` — `10.0.20.154/24` — default gateway `10.0.20.1`.

## Firewall and 10.0.20.0/24 management network

- **MIAM-00116** is the firewall appliance. The asset spreadsheet still describes it as pfSense, while the current firewall screenshot shows **OPNsense 26.7.3**.
- Current firewall interfaces shown in the screenshot:
  - `LAN_JetKVM` — `10.0.10.1/24`.
  - `2p5GSwitch` — `10.0.20.1/24`; this is the gateway used by VM 154 and the PDU management addresses.
  - `ISOLATED30` — `10.0.30.1/24`.
  - `MIAM_00133_Tailscale_GW` gateway — `10.0.20.218`.
- Spreadsheet wiring note: firewall 2.5G interface (third port from right / port 2) connects to **MIAM-00170 2.5G switch port 1**.

## PDU management addresses and DHCP reservations

| Asset | Description | Reserved IP | MAC | Ethernet switch | Switch port |
|---|---|---:|---|---|---:|
| MIAM-00151 | TripLite MV30HVNet - GPU Server #111 & #143 | 10.0.20.151 | 00:06:67:26:84:3f | MIAM-00120 | 21 |
| MIAM-00152 | TripLite MV30HVNet - GPU Server #112 & #144 | 10.0.20.152 | 00:06:67:24:e7:ea | MIAM-00120 | 22 |
| MIAM-00153 | TripLite MV30HVNet - Networking | 10.0.20.153 | 00:06:67:24:e7:f4 | MIAM-00120 | 23 |

Create DHCP reservations on the OPNsense DHCP service serving `10.0.20.0/24`, so each PDU always receives the address above.

## MIAM-00153 power map from the 2026-09-06 spreadsheet

Protected from OFF / REBOOT: **3, 4, 5, 6, 9**. JetKVM outlet 12 is intentionally not protected.

| Outlet | Asset / load |
|---:|---|
| 3 | MIAM-00116 - OPNsense firewall |
| 4 | MIAM-00170 - 2.5G MokerLink switch |
| 5 | MIAM-00171 - 10G MokerLink switch |
| 6 | MIAM-00120 - 1G MokerLink switch |
| 7 | MIAM-00147 - NUC 11 Pro |
| 8 | MIAM-00148 - OWC ThunderBay 8 / DAS |
| 9 | MIAM-00133 - Proxmox / Tailscale routing host |
| 10 | MIAM-00149 - Minisforum unified-memory device |
| 11 | MIAM-00100 - Minisforum MS-01 |
| 12 | MIAM-00172 - JetKVM |
| 15 | MIAM-00176 - Backup fans for Servers 1,2,3,4 |
| 16 | MIAM-00175 - Middle-rack fan for 10G & 2.5G switches, MIAM-00147 and MIAM-00149 |
| 17 | MIAM-00177 - 45W 7-port USB hub for Wyze cameras / Layla sensor |
| 18 | MIAM-00180 - 2000W 240V-to-120V inverter / air purifier |
| 19 | MIAM-00178 - Rugged LACIE 8TB power supply |
| 20 | MIAM-00179 - USB 3.1 hub for MIAM-00100 + Rugged LACIE drives |
| 23 | MIAM-00174 - Top-rack fan for Dell 7010s, firewall, 1G switch, DAS, MIAM-00100 |
| 24 | MIAM-00173 - KYY 1080p monitor / JetKVM / KVM HDMI splitter |

## PowerAlert control backend

The PDU dashboard uses the physically verified legacy PowerAlert SSH menu on TCP/22:

`1 Devices -> 5 Loads -> 1 Configuration -> outlet`

- ON/OFF: option `3`, then lowercase `y`, pause, Enter.
- Cycle/reboot: option `4`, then lowercase `y`, pause, Enter.
- Each operation is verified through a fresh SSH session.
- Per-PDU command lock remains in force; individual command safety timeout is 60 seconds.
