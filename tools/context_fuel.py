#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context_fuel.py —— 豆阿辰的「上下文油量计」
为什么需要它：模型无法从内部精确数自己当前台面占了多少 token（内省不到），
但同一个 agent 的运行轨迹 trajectory.jsonl 就在磁盘上，可以从外部测量这扇窗
「累计烧了多少流水 / 近期台面有多热 / 工具回包灌了多少」，把“我是不是快满了”
从凭感觉变成看读数，辅助决定要不要收班换窗。

重要诚实边界（别误用）：
- 它测的是磁盘轨迹的规模，不等于平台当前真正激活的台面上下文（那个由产品分层/
  压缩管理、不对外暴露，新话题会截断台面但不清这个文件，所以“累计”会跨话题增长）。
- token 换算只给量级区间（中文约 1 字 ≈ 1 token、混代码/工具回包浮动大），不装精确。
- 分档阈值是拿自己实战（清醒世 vs 混乱世）标定的经验值，不是平台官方数字。

用法：
  python3 context_fuel.py                 # 自动找当前会话所有 agent 的轨迹
  python3 context_fuel.py /path/trajectory.jsonl
  python3 context_fuel.py --recent 200    # 改“近期滚动窗”条数（默认200）
"""
import sys, os, json, glob, time

def iter_records(path):
    with open(path, encoding='utf-8', errors='ignore') as f:
        for ln in f:
            try:
                yield json.loads(ln)
            except Exception:
                continue

def text_of(m):
    c = m.get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for x in c:
            if isinstance(x, dict):
                out.append(x.get('text') or (x.get('content') if isinstance(x.get('content'), str) else '') or '')
        return ' '.join(out)
    return ''

def analyze(path, recent_n):
    n=0; roles={}; h_chars=a_chars=t_chars=0; n_human=0
    recent=[]   # (role, chars) 滚动
    for o in iter_records(path):
        m = o.get('message') or o
        r = m.get('role') or o.get('type') or '?'
        t = text_of(m)
        # 人类真实输入（排除 system-reminder 注入）
        is_human = (r=='user') and t and 'system-reminder' not in t[:40]
        roles[r]=roles.get(r,0)+1; n+=1
        L=len(t)
        if r=='user':
            h_chars+=L
            if is_human: n_human+=1
        elif r=='assistant': a_chars+=L
        elif r=='tool': t_chars+=L
        recent.append((r,L))
        if len(recent)>recent_n: recent.pop(0)
    size=os.path.getsize(path)
    recent_chars=sum(L for _,L in recent)
    recent_tool=sum(L for r,L in recent if r=='tool')
    return dict(path=path,size=size,n=n,roles=roles,n_human=n_human,
                h=h_chars,a=a_chars,t=t_chars,
                recent_chars=recent_chars,recent_tool=recent_tool,recent_n=recent_n)

def grade(recent_chars, total_chars, tool_ratio):
    # 经验分档（针对本工作流：工具回包是膨胀头号来源）
    if recent_chars < 400_000 and tool_ratio < 0.85:
        return "清醒｜台面轻，可正常推进"
    if recent_chars < 1_200_000:
        return "中后段｜开始收束，别再开新大工程，准备交班单"
    return "该换窗｜近期台面已重，主动开新话题+读总门牌和交班单，别硬撑"

def fmt(n): return f"{n/10000:.1f}万字" if n>=10000 else f"{n}字"

def main():
    recent_n=200
    args=[x for x in sys.argv[1:] if not x.startswith('--')]
    if '--recent' in sys.argv:
        i=sys.argv.index('--recent')
        try: recent_n=int(sys.argv[i+1])
        except Exception: pass
    if args:
        paths=args
    else:
        cands=glob.glob('/home/user/.doubao/agent_mode/workspace/.sessions/*/agents/*/system/trajectory.jsonl')
        paths=sorted(cands,key=lambda p:os.path.getmtime(p),reverse=True)
    if not paths:
        print("没找到 trajectory.jsonl，可手动传路径"); return
    for p in paths:
        d=analyze(p,recent_n)
        total=d['h']+d['a']+d['t']
        tr=d['t']/max(total,1)
        mtime=time.strftime('%m-%d %H:%M',time.localtime(os.path.getmtime(p)))
        print("="*64)
        print("轨迹:",p)
        print(f"最后写入 {mtime}｜文件 {d['size']/1024/1024:.2f}MB｜记录 {d['n']} 行｜你的真实消息 {d['n_human']} 条")
        print(f"累计流水: 你 {fmt(d['h'])} ｜ 我输出 {fmt(d['a'])} ｜ 工具回包 {fmt(d['t'])}（工具回包占总流水 {tr*100:.0f}%，约为你打字量的 {d['t']/max(d['h'],1):.0f} 倍）")
        print(f"最近 {d['recent_n']} 条记录≈当前台面热度: {fmt(d['recent_chars'])}，其中工具回包 {fmt(d['recent_tool'])}")
        print("油量判断 →", grade(d['recent_chars'], total, tr))
        print("注: 精确台面占用平台不暴露, 此为外部代理读数; token只看量级(中文字≈token)。")

if __name__=='__main__':
    main()
