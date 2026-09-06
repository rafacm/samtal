# Flashing a board

Writing the xiaozhi firmware onto a board over USB. Nothing here is
specific to one board except the name of the archive you download, so
this page is the procedure and each board's own guide carries what
that board does once it is running.

Upstream publishes a [beginner's flashing
guide](https://ccnphfhqs21z.feishu.cn/wiki/Zpz4wXBtdimBrLk25WdcXzxcnNS)
of its own, in Chinese, which walks a board onto the official
xiaozhi.me service. This page is the same act aimed at a server you
run, and it says what that costs on the board.

`esptool` is GPL-2.0, so vinga shells out to it rather than depending
on it. `uvx` runs it without installing anything, the same way the
CLI's own one-off spelling works.

## First, what is on the board already

A board that already runs xiaozhi needs no new firmware to reach your
server: the server's address is one key in its NVS rather than a
property of the build. What a board ships with is not a safe guess,
though. A Waveshare ESP32-S3-Touch-LCD-1.54 bought in 2026 arrived
running the vendor's own `01_factory` demo, which speaks no part of
this protocol.

The board answers the question itself. Every ESP-IDF application
carries a descriptor 0x20 bytes into its partition, and the partition
table at `0x8000` says where that partition is:

```sh
uvx --from esptool esptool --chip esp32s3 --port /dev/cu.usbmodem1101 \
    --baud 460800 read-flash 0x8000 0x1000 parttable.bin
```

Read the app partition's first 0x100 bytes at the offset that table
gives (`factory` or `ota_0`, whichever the table holds), and the
descriptor's `project_name` and `version` fields are at byte 48 and
byte 16 of it. `xiaozhi` and `2.4.0` mean there is nothing to do here;
anything else means flashing.

## Get the image

Upstream publishes one archive per board on its [releases
page](https://github.com/78/xiaozhi-esp32/releases), named for the
version and the board, and each unpacks to a single `merged-binary.bin`
covering the bootloader, the partition table and the application.

```sh
unzip v2.4.0_waveshare-esp32-s3-touch-lcd-1.54.zip   # -> merged-binary.bin
```

## Back up what is there

Flashing at `0x0` replaces everything, and a vendor's demo image is
published nowhere: the board in front of you is the only copy. Take it
before writing, named so a later reader knows which board it came off:

```sh
uvx --from esptool esptool --chip esp32s3 --port /dev/cu.usbmodem1101 \
    --baud 460800 read-flash 0x0 0x1000000 stock-<mac>-<date>.bin
```

A dump that ran to completion can still be noise. It is worth checking
rather than trusting: the file is exactly the size asked for, byte
`0x0` is `0xE9` (an image header), and `0x8000` starts `AA 50` (a
partition table).

Read the MAC to name it with, which also proves the port is right:

```sh
uvx --from esptool esptool --chip esp32s3 --port /dev/cu.usbmodem1101 read-mac
```

## Write it

```sh
uvx --from esptool esptool --chip esp32s3 --port /dev/cu.usbmodem1101 \
    --baud 460800 write-flash 0x0 merged-binary.bin
```

`esptool` compresses on the way out, verifies the written hash, and
resets the board. About forty seconds for a 10 MB image.

Confirm what is running afterwards by reading the descriptor again,
rather than trusting the file that was sent: the partition table has
been replaced too, so the app now lives wherever the new table says.

## What flashing costs

**The NVS partition is erased.** The merged image carries blank
padding where NVS lives, so the write clears it: the WiFi credentials,
the `ota_url` pointing at your server, and the device UUID all go. A
board comes up afterwards knowing nothing, which is why
[onboarding](README.md#getting-a-board-onto-your-server) follows every
flash. The MAC is not stored there and does not change, so a board a
server already knows is still the same device to it.

**The partition layout changes.** A vendor demo may use a `factory`
partition where xiaozhi uses two OTA slots, so this is a whole-image
write rather than an application update.

## Two traps

**Use 460800, not 921600.** The faster rate carried a 16 KB NVS read
without complaint and then failed partway through a 16 MB one with
`Serial data stream stopped: Possible serial noise or corruption`,
writing no file at all. Long transfers are where it shows.

**The port name moves.** `/dev/cu.usbmodem1101` and
`/dev/cu.usbmodem101` are the same board on the same machine across
replugs, so read `ls /dev/cu.*` rather than reusing what a previous
session wrote down.

## Putting a board back

A kept backup is written the same way the firmware was, and restores
the partition table with it:

```sh
uvx --from esptool esptool --chip esp32s3 --port /dev/cu.usbmodem1101 \
    --baud 460800 write-flash 0x0 stock-<mac>-<date>.bin
```

That is also what makes trying vinga reversible on a board that came
with something else on it.
