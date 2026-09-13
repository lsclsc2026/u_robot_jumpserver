import concurrent.futures, hashlib, json, pathlib, shutil, subprocess, tarfile, io
base=pathlib.Path(__file__).resolve().parent
manifest=json.loads((base/'ansible-registry-manifest.json').read_text())
item=json.loads((base/'ansible-image.json').read_text())
cache=base/'ansible-blobs';cache.mkdir(exist_ok=True)
source=base/'images'/item['file'];limit=source.stat().st_size
descriptors=[manifest['config'],*manifest['layers']]
by_hash={d['digest'].split(':')[1]:d for d in descriptors}
def checksum(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(8*1024*1024),b''):h.update(c)
 return h.hexdigest()
with tarfile.open(source) as t:
 while True:
  try:m=t.next()
  except tarfile.ReadError:break
  if m is None or m.offset_data+m.size>limit:break
  key=m.name.removeprefix('sha256:').removesuffix('.tar.gz')
  if key not in by_hash:continue
  target=cache/key
  with target.open('wb') as f:shutil.copyfileobj(t.extractfile(m),f)
  assert checksum(target)==key
  print('Reused verified blob',key[:12],flush=True)
def fetch(d):
 key=d['digest'].split(':')[1];p=cache/key
 if not p.exists():
  with p.open('wb') as f:subprocess.run([str(base/'bin/crane'),'blob','jumpserver/ansible-executor@'+d['digest']],stdout=f,check=True)
 assert p.stat().st_size==d['size'] and checksum(p)==key
 print('Blob verified',key[:12],flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(fetch,descriptors))
config_name=manifest['config']['digest'].split(':')[1]+'.json'
layers=[d['digest'].split(':')[1]+'.tar.gz' for d in manifest['layers']]
output=base/'images/jumpserver_ansible-executor_parallel.tar'
with tarfile.open(output,'w') as t:
 t.add(cache/manifest['config']['digest'].split(':')[1],arcname=config_name)
 for d,name in zip(manifest['layers'],layers):t.add(cache/d['digest'].split(':')[1],arcname=name)
 data=json.dumps([{'Config':config_name,'RepoTags':[item['ref']],'Layers':layers}]).encode()
 ti=tarfile.TarInfo('manifest.json');ti.size=len(data);t.addfile(ti,io.BytesIO(data))
item['sha256']=checksum(output);item['size']=output.stat().st_size
(base/'ansible-image-parallel.json').write_text(json.dumps(item,indent=2)+'\n')
print('PARALLEL_ARCHIVE_COMPLETE',item['sha256'],flush=True)
