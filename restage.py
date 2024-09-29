import argparse
import os
import struct

class BufReader:
    def __init__(self, file):
        self.file = file

    def read(self, size):
        return self.file.read(size)

    def align(self, n):
        cur = self.cur()
        aligned = ((cur + (n - 1)) // n) * n
        self.read(aligned - cur)

    def unpack(self, fmt):
        raw = self.file.read(struct.calcsize(fmt))
        data = struct.unpack(fmt, raw)

        if len(data) == 1:
            data = data[0]

        return data

    def seek(self, off):
        self.file.seek(off)

    def cur(self):
        return self.file.tell()

class BufWriter:
    def __init__(self, file):
        self.file = file

    def write(self, data):
        self.file.write(data)

    def align(self, n):
        cur = self.cur()
        aligned = ((cur + (n - 1)) // n) * n
        
        self.write(b'\x00' * (aligned - cur))

    def pack(self, fmt, *args):
        raw = struct.pack(fmt, *args)
        self.file.write(raw)

    def seek(self, off):
        self.file.seek(off)

    def cur(self):
        return self.file.tell()

def strcode(string):
    code = 0

    for s in string.encode("EUC-JP"):
        code = (code >> 11) | (code << 5)
        code &= 0xffff
        code += s
        code &= 0xffff

    return code

def pack_stage(args, stage, buf):
    if args.verbose >= 1:
        print(f"packing {stage}...")
    
    section = None

    files = []
    with open(f"{stage}/data.cnf") as config:
        for line in config.readlines():
            line = line.strip()

            if line == ".resident":
                assert section == None
                section = 'r'
                continue

            if line == ".nocache":
                assert section == None or section == 'r'
                section = 'n'
                continue

            if line == ".cache":
                assert section == 'r' or section == 'n'
                section = 'c'
                continue

            if line == '.sound':
                assert section == 'c'
                section = 's'
                continue

            assert section

            (name, ext) = line.split('.')
            size = os.path.getsize(f"{stage}/{line}")
            files.append((name, ext, section, size))

    if args.verbose >= 2:
        print("files:")
        for (name, ext, section, size) in files:
            print(f"{name}.{ext} {section} {size}")

    start = buf.cur()
    buf.seek(start + 4)

    in_cache = False
    cache_size = 0

    for i in range(len(files)):
        (name, ext, section, size) = files[i]

        if not in_cache and section == 'c':
            in_cache = True

        if ext == 'dar':
            buf.pack("<H", 0x0000)
        else:
            buf.pack("<H", strcode(name))

        if ext == 'bin':
            buf.pack("<B", ord('s'))
        else:
            buf.pack("<B", ord(section))

        buf.pack("<B", ord(ext[0]))

        if in_cache:
            buf.pack("<I", cache_size)
            cache_size += size
        else:
            buf.pack("<I", size)

        if in_cache and (i == (len(files) - 1) or files[i + 1][2] != 'c'):
            in_cache = False

            buf.pack("<H", 0x0000)
            buf.pack("<B", ord('c'))
            buf.pack("<B", ord('\xff'))
            buf.pack("<I", cache_size)

    buf.align(2048)

    for i in range(len(files)):
        (name, ext, section, size) = files[i]
        
        with open(f"{stage}/{name}.{ext}", "rb") as file:
            buf.write(file.read())

            if section != 'c':
                buf.align(2048)
            elif i == (len(files) - 1) or files[i + 1][2] != 'c':
                buf.align(2048)

    header = 4 + len(files) * 8
    header = ((header + 2047) // 2048) * 2048

    total = buf.cur()

    if len(files) > 255:
        print(f"WARNING: file header size is {header}, expected 2048")

    buf.seek(start)
    buf.pack("<h", header // 2048)
    buf.pack("<h", (total - start) // 2048)
    buf.seek(total)

    if args.verbose:
        print(f"header size: {header:x}")
        print(f"total size: {total:x}")

    return total - start

def pack_dir(args):
    stages = []
    with open("stage_list.txt") as stage_list:
        for stage in stage_list.readlines():
            stages.append(stage.strip())

    outf = open(args.output, "wb")
    buf = BufWriter(outf)

    header_size = len(stages) * 12
    buf.pack("<I", header_size)

    buf.seek(((header_size + 2047) // 2048) * 2048)

    sizes = []
    for stage in stages:
        size = pack_stage(args, stage, buf)
        sizes.append((stage, size))

        buf.align(2048)

    buf.seek(4)

    off = 1
    for (stage, size) in sizes:
        buf.pack("8s", stage.encode('ascii'))
        buf.pack("<I", off)

        if args.verbose:
            print(f"writing {stage} at offset {off * 2048}...")

        off += (size + 2047) // 2048

    outf.close()

    print(f"successfully wrote {args.output}!")

def build_dictionary(file):
    table = {}

    with open(file) as names:
        for name in names:
            name = name.strip().split('|')

            if len(name) == 2:
                table[name[0]] = name[1]

    return table

def write_stage_config(files, stage):
    section = None
    
    with open(f"{stage}/data.cnf", "w") as config:
        for (name, mode, _) in files:
            assert mode in "cnrs"

            if name == "cache_end":
                continue

            if ".bin" in name:
                mode = 'n'
                
            if section != mode:
                if mode == 'c':
                    config.write(".cache\n")
                elif mode == 'n':
                    config.write(".nocache\n")
                elif mode == 'r':
                    config.write(".resident\n")
                else:
                    config.write(".sound\n")
                section = mode
    
            config.write(f"{name}\n")

def unpack_stage(args, table, stage, buf):
    if args.verbose:
        print(f"unpacking {stage}...")

    start = buf.cur()

    header = buf.unpack("<h")
    total = buf.unpack("<h")

    if header != 1:
        print(f"WARNING: file header size is {header * 2048}, expected 2048")

    if args.verbose:
        print(f"header size: {header * 2048:x}")
        print(f"total size: {total * 2048:x}")
        print("")

    files = []

    mdl_count = 1
    tex_count = 1
    dar_name = "stg" # TODO: rank uses both stg_tex1 and res_tex1

    if args.verbose >= 2:
        print("files:")

    while True:
        name = buf.unpack("<H")
        mode = chr(buf.unpack("<B"))
        ext = chr(buf.unpack("<B"))
        size = buf.unpack("<I")

        if mode == '\x00':
            break

        assert mode in "cnrs"
        assert ext.islower() or ext == '\xFF'

        if mode == 'r':
            dar_name = "res"

        if name != 0x0000:
            name = table[f"{name:04x}.{ext}"]
        elif name == 0x0000 and ext != '\xFF' and mode in "cr":
            name = f"{dar_name}_mdl{mdl_count}.dar"
            mdl_count += 1
        elif name == 0x0000 and ext != '\xFF' and mode == 'n':
            name = f"{dar_name}_tex{tex_count}.dar"
            tex_count += 1
        else:
            name = "cache_end"

        if args.verbose >= 2:
            if mode == 'c':
                print(f"{name} offset {size:08x}")
            elif args.verbose:
                print(f"{name} size {size:08x}")

        files.append((name, mode, size))

    if args.verbose >= 2:
        print("")

    buf.seek(start + header * 2048)

    try:
        os.mkdir(stage)
    except FileExistsError:
        None

    write_stage_config(files, stage)

    for i in range(len(files)):
        (name, mode, size) = files[i]

        if name == "cache_end":
            buf.align(2048)
            continue

        if mode == 'c':
            (_, _, nextsize) = files[i + 1]
            size = nextsize - size

        with open(f"{stage}/{name}", "wb") as outf:            
            outf.write(buf.read(size))

            if mode != 'c':
                buf.align(2048)

def unpack_dir(args):
    file = open(args.input, "rb")
    buf = BufReader(file)

    file_size = os.path.getsize(args.input)

    header_size = buf.unpack("<I")
    assert((header_size % 12) == 0)

    n_stages = header_size // 12

    stages = []
    for i in range(n_stages):
        name = buf.unpack("8s").decode('ascii').rstrip("\x00")
        off = buf.unpack("<I") * 2048
        stages.append((name, off))

    stage_list = open("stage_list.txt", "w")

    table = build_dictionary("dict.txt")

    for (name, off) in stages:
        stage_list.write(f"{name}\n")

        if not args.stage or args.stage == name:
            buf.seek(off)
            unpack_stage(args, table, name, buf)

    stage_list.close()

    if args.verbose:
        print(f"{n_stages} stage files written!")

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("-i", "--input",
                        help="dir file to unpack from",
                        default="STAGE.DIR")

    parser.add_argument("-o", "--output",
                        help="dir file to pack to",
                        default="NEW_STAGE.DIR")

    parser.add_argument("-s", "--stage",
                        help="stage file to unpack, otherwise unpack all")

    parser.add_argument("-p", "--pack",
                        help="pack a stage directory using stage_list.txt",
                        action="store_true")

    parser.add_argument("-v", "--verbose",
                        help="enable verbose output",
                        action="count",
                        default=2)

    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()

    if args.pack:
        pack_dir(args)
    else:
        unpack_dir(args)
