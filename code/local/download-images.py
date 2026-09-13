import concurrent.futures, hashlib, json, pathlib, subprocess
base=pathlib.Path(__file__).resolve().parent
crane=str(base/'bin/crane')
refs=['jumpserver/core:v4.10.19-ce','jumpserver/koko:v4.10.19-ce','jumpserver/web:v4.10.19-ce','postgres:16.15-bookworm','redis:7.4.10-bookworm']
def run(*args):
 return subprocess.check_output([crane,*args],text=True).strip()
def fetch(ref):
 digest=run('digest','--platform','linux/amd64',ref)
 pinned=ref.rsplit(':',1)[0]+'@'+digest
 manifest=json.loads(run('manifest','--platform','linux/amd64',pinned))
 filename=ref.replace('/','_').replace(':','_')+'.tar'
 path=base/'images'/filename
 print('Downloading '+ref+' '+digest,flush=True)
 if not path.exists():
  subprocess.run([crane,'pull','--platform','linux/amd64',pinned,str(path)],check=True)
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(8*1024*1024),b''): h.update(chunk)
 checksum=h.hexdigest()
 result={'ref':ref,'manifest_digest':digest,'image_id':manifest['config']['digest'],'file':filename,'sha256':checksum,'size':path.stat().st_size}
 print('Verified archive '+ref+' bytes='+str(result['size']),flush=True)
 return result
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 results=list(pool.map(fetch,refs))
(base/'images/manifest.json').write_text(json.dumps(results,indent=2)+'\n')
print('OFFLINE_DOWNLOAD_COMPLETE',flush=True)
