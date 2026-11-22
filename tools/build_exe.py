"""Repack this application's original Go launcher without altering its machine code.

The original executable has a Go-embedded ZIP in a global []byte. This builder
places the new ZIP in a read-only PE section and retargets that existing slice.
Its original ASLR relocation is preserved. No launcher, authentication or
runtime-installation behavior is replaced.

Reference: https://learn.microsoft.com/en-us/windows/win32/debug/pe-format
Usage: python tools/build_exe.py ORIGINAL.exe OUTPUT.exe
"""
from pathlib import Path
import io, struct, zipfile, hashlib, sys
from pe_resources import icon_resources

ROOT=Path(__file__).resolve().parents[1]

def align(n,a):return (n+a-1)//a*a

def build(original,output):
    binary=bytearray(Path(original).read_bytes())
    pe=struct.unpack_from('<I',binary,0x3c)[0]
    assert binary[pe:pe+4]==b'PE\0\0','Not a PE executable'
    coff=pe+4; opt=pe+24
    machine,count=struct.unpack_from('<HH',binary,coff)
    assert machine==0x8664 and struct.unpack_from('<H',binary,opt)[0]==0x20b
    optsize=struct.unpack_from('<H',binary,coff+16)[0]
    section_table=opt+optsize
    base=struct.unpack_from('<Q',binary,opt+24)[0]
    va_align,file_align=struct.unpack_from('<II',binary,opt+32)
    sections=[]
    for i in range(count):
        s=struct.unpack_from('<8sIIIIIIHHI',binary,section_table+40*i)
        sections.append(s)
    def off_to_rva(off):
        for name,vsize,rva,size,raw,*_ in sections:
            if raw<=off<raw+size:return rva+off-raw
        raise ValueError('Unmapped offset')
    def rva_to_off(rva):
        for name,vsize,start,size,raw,*_ in sections:
            if start<=rva<start+size:return raw+rva-start
        raise ValueError('Unmapped RVA')
    assert section_table+40*(count+2)<=min(s[4] for s in sections if s[4]),'No section header space'
    assert struct.unpack_from('<II',binary,opt+112+2*8)==(0,0),'Inspect existing resources before replacing them'
    # The input is the supplied launcher, not an arbitrary executable.
    assert hashlib.sha256(binary).hexdigest()== '600d07ee2cb85e2852c7ccaeb871494f05b49d3dfccff835ca8e06d8770d0d0b', 'Unexpected launcher; inspect it before repacking'
    end=binary.rfind(b'PK\x05\x06')
    assert end!=-1
    end+=22+struct.unpack_from('<H',binary,end+20)[0]
    old_zip=zipfile.ZipFile(io.BytesIO(binary[:end]))
    assert old_zip.read('VERSION').strip()==b'2.9.6.5'
    first=min(f.header_offset for f in old_zip.infolist())
    old_length=end-first
    old_ptr=base+off_to_rva(first)
    signature=struct.pack('<QQQ',old_ptr,old_length,old_length)
    assert binary.count(signature)==1,'Expected one embedded ZIP slice'
    slice_off=binary.index(signature)
    slice_rva=off_to_rva(slice_off)
    # Confirm the pointer is relocated by the Windows loader on nonpreferred bases.
    reloc_rva,reloc_len=struct.unpack_from('<II',binary,opt+112+5*8)
    cursor=rva_to_off(reloc_rva); stop=cursor+reloc_len; has_reloc=False
    while cursor<stop:
        page,size=struct.unpack_from('<II',binary,cursor)
        assert size>=8
        for off in range(cursor+8,cursor+size,2):
            entry=struct.unpack_from('<H',binary,off)[0]
            if entry>>12==10 and page+(entry&0xfff)==slice_rva:has_reloc=True
        cursor+=size
    assert has_reloc,'Embedded pointer needs an existing DIR64 relocation'
    payload=io.BytesIO()
    included=[]
    with zipfile.ZipFile(payload,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted(ROOT.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.pyc','.exe','.zip'):continue
            rel=path.relative_to(ROOT)
            if rel.parts[0] in ('tools','tests'):continue
            z.write(path,rel.as_posix());included.append(rel.as_posix())
    data=payload.getvalue()
    new_rva=align(max(s[2]+max(s[1],s[3]) for s in sections),va_align)
    new_raw=align(len(binary),file_align);new_size=align(len(data),file_align)
    binary.extend(b'\0'*(new_raw+new_size-len(binary)))
    binary[new_raw:new_raw+len(data)]=data
    struct.pack_into('<8sIIIIIIHHI',binary,section_table+40*count,
                     b'.ares\0\0\0',len(data),new_rva,new_size,new_raw,0,0,0,0,0x40000040)
    resource_rva=align(new_rva+len(data),va_align)
    resource_data=icon_resources((ROOT/'app/assets/app.ico').read_bytes(),resource_rva,(ROOT/'VERSION').read_text().strip())
    resource_raw=align(len(binary),file_align);resource_size=align(len(resource_data),file_align)
    binary.extend(b'\0'*(resource_raw+resource_size-len(binary)))
    binary[resource_raw:resource_raw+len(resource_data)]=resource_data
    struct.pack_into('<8sIIIIIIHHI',binary,section_table+40*(count+1),
                     b'.rsrc\0\0\0',len(resource_data),resource_rva,resource_size,resource_raw,0,0,0,0,0x40000040)
    struct.pack_into('<II',binary,opt+112+2*8,resource_rva,len(resource_data))
    struct.pack_into('<H',binary,coff+2,count+2)
    struct.pack_into('<I',binary,opt+8,struct.unpack_from('<I',binary,opt+8)[0]+new_size+resource_size)
    struct.pack_into('<I',binary,opt+56,align(resource_rva+len(resource_data),va_align))
    struct.pack_into('<QQQ',binary,slice_off,base+new_rva,len(data),len(data))
    struct.pack_into('<I',binary,opt+64,0)
    # PE checksum excludes its own four-byte field.
    checksum=0
    for i in range(0,len(binary),2):
        word=binary[i]+((binary[i+1] if i+1<len(binary) else 0)<<8)
        checksum+=word;checksum=(checksum&0xffff)+(checksum>>16)
    checksum=(checksum&0xffff)+(checksum>>16)
    struct.pack_into('<I',binary,opt+64,(checksum&0xffff)+len(binary))
    recovered=zipfile.ZipFile(io.BytesIO(binary[new_raw:new_raw+len(data)]))
    assert recovered.testzip() is None
    assert recovered.read('VERSION').strip()==(ROOT/'VERSION').read_bytes().strip()
    assert 'app/modern_ui.py' in recovered.namelist()
    assert 'app/ares_command_relay.py' in recovered.namelist()
    assert 'app/ares_repunish.py' in recovered.namelist()
    assert 'app/repunish_ui.py' in recovered.namelist()
    for section in sections:
        if section[0].rstrip(b'\0')==b'.text':
            raw,size=section[4],section[3]
            assert binary[raw:raw+size]==Path(original).read_bytes()[raw:raw+size]
    for name in included:
        assert recovered.read(name)==(ROOT/name).read_bytes(),name
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(binary)
    print(f'{output.name}: {len(binary)} bytes; {len(included)} files; ZIP and ASLR relocation verified')

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit(__doc__)
    build(sys.argv[1],sys.argv[2])
