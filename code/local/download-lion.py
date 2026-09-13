import concurrent.futures,hashlib,json,pathlib,subprocess,tarfile,io
b=pathlib.Path(__file__).resolve().parent
crane=str(b/'bin/crane');ref='jumpserver/lion:v4.10.19-ce';digest='sha256:2836cf9a4720213f7b3a81cdd170658fe5ad2ec2a87aafb122fb444908fb8bda'
m=json.loads(subprocess.check_output([crane,'manifest','--platform','linux/amd64','jumpserver/lion@'+digest]))
cache=b/'lion-blobs';cache.mkdir(exist_ok=True)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(8388608),b''):h.update(c)
 return h.hexdigest()
def fetch(d):
 key=d['digest'].split(':')[1];p=cache/key
 if not p.exists() or p.stat().st_size!=d['size'] or sha(p)!=key:
  with p.open('wb') as f:subprocess.run([crane,'blob','jumpserver/lion@'+d['digest']],stdout=f,check=True)
 assert p.stat().st_size==d['size'] and sha(p)==key
 print('Verified blob',d['size'],flush=True)
ds=[m['config'],*m['layers']]
print('Lion compressed bytes',sum(d['size'] for d in ds),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(fetch,ds))
out=b/'images/jumpserver_lion_v4.10.19-ce.tar';cn=m['config']['digest'].split(':')[1]+'.json';layers=[d['digest'].split(':')[1]+'.tar.gz' for d in m['layers']]
with tarfile.open(out,'w') as t:
 t.add(cache/m['config']['digest'].split(':')[1],arcname=cn)
 for d,n in zip(m['layers'],layers):t.add(cache/d['digest'].split(':')[1],arcname=n)
 data=json.dumps([{'Config':cn,'RepoTags':[ref],'Layers':layers}]).encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);t.addfile(info,io.BytesIO(data))
subprocess.run([crane,'validate','--tarball',str(out)],check=True)
item={'ref':ref,'manifest_digest':digest,'image_id':m['config']['digest'],'file':out.name,'size':out.stat().st_size,'sha256':sha(out)}
(b/'lion-image.json').write_text(json.dumps(item,indent=2))
print('LION_DOWNLOAD_COMPLETE',flush=True)
