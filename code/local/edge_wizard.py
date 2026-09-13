#!/usr/bin/env python3
"""Interactive edge onboarding: explicit pauses for Tailscale and JumpServer."""
import argparse, json, os, pwd, shlex, shutil, subprocess, sys, tempfile
from pathlib import Path
BASE=Path(__file__).resolve().parent

def run(*args,check=True,capture=False):
    return subprocess.run(args,check=check,text=True,capture_output=capture)
def pause(message):
    print('\n'+message,flush=True)
    answer=input('完成后按回车继续；输入 q 安全退出，稍后可按阶段继续：').strip().lower()
    if answer=='q': sys.exit(0)

def ensure_tailscale():
    if not shutil.which('tailscale'):
        print('未安装 Tailscale，正在自动安装官方稳定版……',flush=True)
        if not shutil.which('curl'):
            run('apt-get','update')
            run('apt-get','--no-remove','install','-y','curl','ca-certificates')
        with tempfile.TemporaryDirectory(prefix='jms-tailscale-') as folder:
            installer=Path(folder)/'install.sh'
            run('curl','--fail','--show-error','--location','--proto','=https','--proto-redir','=https','--connect-timeout','20','--max-time','180','https://tailscale.com/install.sh','-o',str(installer))
            run('sh',str(installer))
        if not shutil.which('tailscale'):
            raise RuntimeError('官方安装程序执行后仍未找到 tailscale，停止。')
    else:
        print('检测到 Tailscale，跳过安装。',flush=True)
    run('systemctl','enable','--now','tailscaled')
    print('正在执行 tailscale up；如显示授权链接，请在浏览器登录堡垒机所在网络，终端会等待。',flush=True)
    run('tailscale','up')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--user',required=True)
    p.add_argument('--bastion-ip',required=True)
    p.add_argument('--name',required=True)
    p.add_argument('--from-stage',choices=['tailscale','desktop','assets','finalize'],default='tailscale')
    p.add_argument('--install-gnome',action='store_true')
    p.add_argument('--replace-startwm',action='store_true')
    a=p.parse_args()
    if os.geteuid()!=0 or not sys.stdin.isatty(): raise RuntimeError('请通过交互终端使用 sudo python3 执行。')
    pwd.getpwnam(a.user)
    release=dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID','').strip(chr(34))!='ubuntu' or release.get('VERSION_ID','').strip(chr(34))!='22.04':
        raise RuntimeError('此版本仅支持 Ubuntu 22.04，未执行安装。')
    print('EDGE_WIZARD_VERSION: 2026-09-10.2 (automatic Tailscale installation)',flush=True)
    for script in ['setup_edge_desktop.py','edge_finalize.py']:
        if not (BASE/script).is_file(): raise RuntimeError('缺少同目录文件 '+script)
    from setup_edge_desktop import select_ip, asset_report
    stages=['tailscale','desktop','assets','finalize'];start=stages.index(a.from_stage)
    if start==0:
        print('阶段 1/4：Tailscale。不会自动退出已有 tailnet。',flush=True)
        ensure_tailscale()
        while True:
            r=run('tailscale','status','--json',capture=True,check=False)
            status={}
            try:
                status=json.loads(r.stdout)
                edge=select_ip(status,'auto',a.bastion_ip)
                print('Tailscale 本机地址：'+edge)
                for w in status.get('Health') or []: print('健康告警（仍需检查）：'+w)
                break
            except (ValueError,RuntimeError) as e:
                print('尚未满足条件：'+str(e))
                if 'status' in locals() and status.get('BackendState')=='Running':
                    pause('当前 tailnet 看不到指定堡垒机。请在另一终端核实组织/访问策略。\n只有确定迁移时才手动执行 sudo tailscale logout 和 sudo tailscale up。')
                else:
                    print('重新发起 Tailscale 授权，请完成浏览器登录。',flush=True)
                    run('tailscale','up')
        pause('请在堡垒机服务器测试：\nssh '+shlex.quote(a.user+'@'+edge)+'\n确认 SSH 成功后继续。密码只在 SSH 提示中输入。')
    status=json.loads(run('tailscale','status','--json',capture=True).stdout)
    edge=select_ip(status,'auto',a.bastion_ip)
    if start<=1:
        print('阶段 2/4：下位机桌面部署。',flush=True)
        cmd=['/usr/bin/python3',str(BASE/'setup_edge_desktop.py'),'--user',a.user,'--bastion-ip',a.bastion_ip,'--name',a.name]
        if a.install_gnome: cmd.append('--install-gnome')
        if a.replace_startwm: cmd.append('--replace-startwm')
        run(*cmd)
        pause('前提检查通过。继续将安装依赖、备份并配置 xrdp，可能中断已有 RDP 会话。')
        run(*cmd,'--apply')
    if start<=2:
        print('阶段 3/4：JumpServer 页面配置。')
        print(json.dumps(asset_report(a.name,a.user,edge),ensure_ascii=False,indent=2))
        pause('请在 JumpServer 创建/更新上述两个资产，录入系统账号凭据并授权用户组。\n验证 SSH、桌面、开发目录，以及 SSH 和桌面回放。\n这些操作不因按回车而自动完成；确认测试成功后继续。')
    print('阶段 4/4：最后封堵。此向导不在可能是直连的当前会话中自动断开 SSH。')
    cmd=['sudo','python3',str(BASE/'edge_finalize.py'),'apply','--bastion-ip',a.bastion_ip]
    print('请从 JumpServer 打开本机 SSH 终端，执行：\n'+shlex.join(cmd))
    print('收尾脚本有 15 分钟自动回滚。验证新 SSH/桌面连接后，\n在新的 JumpServer SSH 会话执行：\nsudo python3 /var/lib/jms-edge-finalize/manage.py confirm\n按提示输入 VERIFIED 后，才关闭 NoMachine 并保存防火墙。')
    print('WIZARD_HANDOFF_READY：收尾未自动执行；不代表部署全部完成。')
if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('\n已退出。若正在收尾试运行，不会取消其自动回滚。');sys.exit(130)
    except Exception as e: print('WIZARD_STOPPED: '+str(e),file=sys.stderr);sys.exit(1)
