import json, pathlib, re, shutil, subprocess, time
ROOT=pathlib.Path('/srv/oak')
PUBLIC=ROOT/'web'
PUBLIC.mkdir(exist_ok=True)
while True:
    try:
        r=subprocess.run(['/usr/local/sbin/oak-admin','list'],capture_output=True,text=True,timeout=5)
        match=re.search(r'There are (\d+) of a max of (\d+) players online:(.*)',r.stdout)
        players=[s.strip() for s in match[3].split(',') if s.strip()] if match else []
        messages=[]
        with (ROOT/'server/logs/latest.log').open(errors='replace') as f:
            f.seek(0,2); size=f.tell(); f.seek(max(0,size-256000))
            for line in f:
                chat=re.search(r'^\[([\d:]+)\] \[Server thread/INFO\]: (?:\[Not Secure\] )?<([A-Za-z0-9_]{1,16})> (.*)$',line)
                if chat: messages.append({'time':chat[1],'player':chat[2],'text':chat[3][:1000]})
        disk=shutil.disk_usage(ROOT)
        backups=sorted((ROOT/'backups').glob('oak-*.tar.gz'))
        data={'updated':time.time(),'online':bool(match),'players':players,'maxPlayers':int(match[2]) if match else 12,'version':'26.3-rc-1','chat':messages[-60:],'disk':{'used':disk.used,'total':disk.total,'free':disk.free},'backup':{'last':backups[-1].stat().st_mtime if backups else None,'count':len(backups),'bytes':sum(p.stat().st_size for p in backups)},'warnings':[]}
        if disk.free<30*1024**3: data['warnings'].append('Pouco espaço livre na VM')
        if not backups or time.time()-backups[-1].stat().st_mtime>27*3600: data['warnings'].append('Backup atrasado')
        unified = ROOT/'control/backup-public.json'
        if unified.exists():
            evidence = json.loads(unified.read_text())
            data['backup'] = evidence
            data['warnings'] = [w for w in data['warnings'] if w != 'Backup atrasado']
            if evidence.get('enabled') and (not evidence.get('last') or time.time()-evidence['last'] > evidence['interval_minutes']*60+1800):
                data['warnings'].append('Backup atrasado')
        if (PUBLIC/'map-warning.json').exists(): data['warnings'].append('Mapa pausado para preservar armazenamento')
        tmp=PUBLIC/'status.tmp'; tmp.write_text(json.dumps(data,ensure_ascii=False)); tmp.chmod(0o644); tmp.replace(PUBLIC/'status.json')
    except Exception as e:
        print(type(e).__name__,str(e),flush=True)
    time.sleep(5)
