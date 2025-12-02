# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

QR Code Fountain Transceiver - a Python-based system for transmitting files via fountain-coded QR codes. Uses fountain codes (LT codes with robust soliton distribution) for rateless erasure coding, enabling reliable file transfer over visual QR code channels.

## Running the Tools

**Transmitter (TX):**
```bash
python qrcode_tx.py <filename> [options]
```
Options: `--chunk-size`, `--extra`, `--size`, `--display-interval`, `--no-display`, `-o/--output`

**Receiver (RX):**
```bash
python qrcode_rx.py [options]
```
Options: `--monitor`, `--region`, `--interval`, `--dedup`, `--quiet`, `--output-dir`, `--no-save`

## Dependencies

- opencv-python (cv2)
- numpy
- mss (screen capture)
- qrcode
- Pillow (PIL)
- Optional: pyzbar (faster QR decoding, falls back to OpenCV if not installed)

## Architecture

### FF.py - Fountain Code Core
- `Fountain`: Encodes data into droplets using robust soliton distribution
- `Glass`: Decodes droplets back to original data using belief propagation
- `Droplet`: Data unit with seed, chunk count, padding, and XOR'd payload
- `robust_solition()`: Generates degree distribution for LT codes
- `randChunkNums()`: Deterministic chunk selection based on seed
- Droplet string format: `seed|num_chunks|padding|base64_data`

### qrcode_tx.py - Transmitter
- Reads file, prepends filename header, generates fountain droplets
- Creates QR code images and optionally displays rotating carousel via Tkinter
- Payload format: `filename\n<binary_data>`

### qrcode_rx.py - Receiver
- Captures screen region, decodes QR codes continuously
- `DropletAutoSaver`: Manages decoding session, assembles files when complete
- Auto-locates QR code region on first detection if no region specified
- Supports pyzbar (preferred) or OpenCV QRCodeDetector

### Multi-file Transfer Protocol
- Manifest file `__FILE_COUNT__` signals number of files to expect
- Files decoded and saved individually as each completes
