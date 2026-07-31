import json, re, subprocess, urllib.request, urllib.parse, datetime, os
TODAY=datetime.date(2026,7,30)

def curl(url, headers=None):
    req=urllib.request.Request(url,headers=headers or {'User-Agent':'Mozilla/5.0'})
    return urllib.request.urlopen(req,timeout=15).read()

def qtx(codes):
    raw=curl('https://qt.gtimg.cn/q='+','.join(codes)).decode('gb18030','replace')
    out={}
    for line in raw.split(';'):
        if '~' not in line: continue
        p=line.split('~')
        key=line.split('=')[0].replace('v_','').strip()
        if len(p)>34:
            out[key]={'name':p[1],'price':float(p[3] or 0),'prev':float(p[4] or 0),'pct':float(p[32] or 0),'ts':p[30] if len(p)>30 else ''}
    return out

def sina():
    codes='gb_dji,gb_ixic,gb_inx,gb_hxc,hf_CL,hf_GC,fx_susdcny'
    b=curl('https://hq.sinajs.cn/list='+codes,{'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
    text=b.decode('latin1','replace')
    out={}
    for ln in text.splitlines():
        m=re.search(r'hq_str_([^=]+)="([^"]*)"',ln)
        if not m: continue
        try:s=m.group(2).encode('latin1').decode('gb18030','ignore')
        except:s=m.group(2)
        out[m.group(1)]=s.split(',')
    return out

def east_calendar():
    nxt=TODAY+datetime.timedelta(days=1)
    params={'reportName':'RPT_CPH_FECALENDAR','pageNumber':1,'pageSize':100,'sortColumns':'START_DATE','sortTypes':'1','filter':f"(END_DATE>='{TODAY}')(START_DATE<'{nxt}')",'source':'WEB','client':'WEB','columns':'START_DATE,END_DATE,FE_CODE,FE_NAME,FE_TYPE,CONTENT,STD_TYPE_CODE,SPONSOR_NAME,CITY'}
    url='https://datacenter-web.eastmoney.com/api/data/v1/get?'+urllib.parse.urlencode(params)
    try:
      d=json.loads(curl(url,{'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/cjrl/'}))
      return ((d.get('result') or {}).get('data') or [])
    except Exception as e:return {'error':str(e)}

def north():
    urls=[
      'https://push2.eastmoney.com/api/qt/kamt/get?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f56,f57,f58',
      'https://vaserviece.10jqka.com.cn/hsgtApi/amountData?amount=100'
    ]
    ret=[]
    for u in urls:
      try:ret.append({'url':u,'body':curl(u).decode('utf8','replace')[:3000]})
      except Exception as e:ret.append({'url':u,'error':str(e)})
    return ret
q=qtx(['sh000001','sz399001','sz399006','sh000688','sz300059','usHXC'])
s=sina()
print(json.dumps({'q':q,'sina':s,'events':east_calendar(),'north':north()},ensure_ascii=False,indent=2))
