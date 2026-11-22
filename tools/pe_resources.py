"""Build an ID-only PE resource tree with a multi-resolution icon and version."""
import struct


def align(n): return (n+3)//4*4


def version_info(version):
    def block(key, value=b'', children=(), text=False):
        key=(key+'\0').encode('utf-16le')
        data=bytearray(b'\0'*6+key)
        data.extend(b'\0'*(align(len(data))-len(data)))
        data.extend(value)
        for child in children:
            data.extend(b'\0'*(align(len(data))-len(data)))
            data.extend(child)
        struct.pack_into('<HHH',data,0,len(data),len(value)//2 if text else len(value),int(text))
        return bytes(data)
    parts=tuple(map(int,version.split('.')))
    assert len(parts)==4 and all(0<=n<=65535 for n in parts)
    high,low=(parts[0]<<16)|parts[1],(parts[2]<<16)|parts[3]
    strings={'CompanyName':'AresMine','FileDescription':'Ares Assistant',
             'FileVersion':version,'ProductVersion':version,'ProductName':'Ares Assistant',
             'OriginalFilename':f'AresAssistant_{version}.exe'}
    table=block('040904B0',children=[block(k,(v+'\0').encode('utf-16le'),text=True) for k,v in strings.items()],text=True)
    fixed=struct.pack('<13I',0xFEEF04BD,0x10000,high,low,high,low,0x3f,0,0x40004,1,0,0,0)
    return block('VS_VERSION_INFO',fixed,[block('StringFileInfo',children=[table],text=True),
        block('VarFileInfo',children=[block('Translation',struct.pack('<HH',0x409,1200))],text=True)])


def icon_resources(ico, rva, version):
    reserved, kind, count=struct.unpack_from('<HHH',ico)
    assert (reserved,kind)==(0,1) and count>0
    images={};group=bytearray(struct.pack('<HHH',0,1,count))
    for i in range(count):
        w,h,colors,res,planes,bpp,size,offset=struct.unpack_from('<BBBBHHII',ico,6+16*i)
        assert offset+size<=len(ico)
        images[i+1]={1033:ico[offset:offset+size]}
        group.extend(struct.pack('<BBBBHHIH',w,h,colors,res,planes,bpp,size,i+1))
    tree={3:images,14:{1:{1033:bytes(group)}},16:{1:{1033:version_info(version)}}}
    data=bytearray();leaves=[]
    def directory(entries):
        offset=len(data)
        data.extend(struct.pack('<IIHHHH',0,0,0,0,0,len(entries))+b'\0'*(8*len(entries)))
        for index,(identifier,child) in enumerate(sorted(entries.items())):
            if isinstance(child,dict):
                child_offset=directory(child)|0x80000000
            else:
                child_offset=len(data);data.extend(b'\0'*16);leaves.append((child_offset,child))
            struct.pack_into('<II',data,offset+16+index*8,identifier,child_offset)
        return offset
    directory(tree)
    for entry,content in leaves:
        data.extend(b'\0'*(align(len(data))-len(data)))
        struct.pack_into('<IIII',data,entry,rva+len(data),len(content),0,0)
        data.extend(content)
    return bytes(data)
