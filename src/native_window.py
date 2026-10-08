"""Native Tk desktop client. No browser, HTTP server or HTML renderer."""
from i18n import ui, set_language, localize_widgets
import argparse
import io
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from tkinter import font as tkfont
from PIL import Image, ImageTk
from app_paths import APP_ROOT
from inspection_runtime import Runtime
from instance_lock import InstanceLock


def model_name(device):
    model, oem = device.get('model', ''), device.get('oem_model', '')
    return f'{oem}（{model}）' if model and oem and model != oem else oem or model or 'Unknown model'


class Desktop:
    def __init__(self, root, runtime):
        self.root, self.runtime = root, runtime
        self.state = None
        self.commands, self.responses = queue.Queue(), queue.Queue(maxsize=4)
        self.closed = False
        self.dialog = None
        self.preview_open = False
        self.preview_waiting_clear = False
        self.notice_handled = False
        self.settings_refresh_action = None
        self.settings_refresh_ready = False
        self.cards = []
        self.last_mode = None
        root.title(ui('ITC-C80 Camera Inspection'))
        root.geometry('1280x820')
        root.minsize(900, 650)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.configure(bg='#eef2f6')
        self.font_family = tkfont.nametofont('TkDefaultFont').actual('family')
        tkfont.nametofont('TkDefaultFont').configure(size=10)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=(self.font_family, 10))
        style.configure('TFrame', background='#eef2f6')
        style.configure('TLabel', background='#eef2f6', foreground='#334155')
        style.configure('Toolbar.TFrame', background='#ffffff')
        style.configure('Toolbar.TLabel', background='#ffffff')
        style.configure('Count.Toolbar.TLabel', font=(self.font_family, 16, 'bold'), foreground='#0f766e')
        style.configure('TButton', padding=(12, 8), background='#ffffff', foreground='#334155', borderwidth=1)
        style.map('TButton', background=[('active', '#e2e8f0')], foreground=[('disabled', '#94a3b8')])
        for name, color, active in [('Primary', '#0f766e', '#115e59'), ('OK', '#15803d', '#166534'), ('NG', '#be123c', '#9f1239')]:
            style.configure(name + '.TButton', background=color, foreground='white', font=(self.font_family, 10, 'bold'))
            style.map(name + '.TButton', background=[('disabled', '#e2e8f0'), ('active', active)], foreground=[('disabled', '#94a3b8')])
        style.configure('Card.TFrame', background='#ffffff')
        style.configure('Card.TLabel', background='#ffffff', foreground='#64748b')
        style.configure('Stage.Card.TLabel', font=(self.font_family, 11, 'bold'), foreground='#0f172a')
        for name, color in [('Idle', '#cbd5e1'), ('Selected', '#2563eb'), ('OK', '#15803d'), ('NG', '#be123c'), ('Running', '#0f766e')]:
            style.configure(name + '.TLabelframe', background='#ffffff', bordercolor=color, borderwidth=2, relief='solid')
            style.configure(name + '.TLabelframe.Label', background='#ffffff', foreground=color, font=(self.font_family, 11, 'bold'))
        style.configure('TNotebook', background='#eef2f6', borderwidth=0)
        style.configure('TNotebook.Tab', padding=(16, 10))
        brand = tk.Frame(root, bg='#0f172a', padx=20, pady=12)
        brand.pack(fill='x')
        tk.Label(brand, text=ui('ITC-C80'), font=(self.font_family, 20, 'bold'), bg='#0f172a', fg='white').pack(side='left')
        tk.Label(brand, text=ui('CAMERA INSPECTION'), font=(self.font_family, 10), bg='#0f172a', fg='#cbd5e1').pack(side='left', padx=18)
        toolbar = ttk.Frame(root, padding=(16, 10), style='Toolbar.TFrame')
        toolbar.pack(fill='x')
        self.start = ttk.Button(toolbar, text=ui('Start inspection'), command=self.start_job, style='Primary.TButton')
        self.start.pack(side='left')
        self.counter = ttk.Label(toolbar, text=ui('OK 0 · NG 0'), style='Count.Toolbar.TLabel')
        self.counter.pack(side='left', padx=12)
        self.mode = tk.StringVar(value='2')
        ttk.Label(toolbar, text=ui('Panel count'), style='Toolbar.TLabel').pack(side='left', padx=(14, 6))
        selector = ttk.Combobox(toolbar, textvariable=self.mode, values=['1','2','4','6'], width=4, state='readonly')
        selector.pack(side='left')
        selector.bind('<<ComboboxSelected>>', lambda e: self.send(action='mode', count=int(self.mode.get())))
        for text, callback in [('Settings', self.settings), ('Logs', self.logs), ('Exit', self.close)]:
            ttk.Button(toolbar, text=ui(text), command=callback).pack(side='right', padx=3)
        row = ttk.Frame(root, padding=(16,14,16,8)); row.pack(fill='x')
        self.inputs = {}
        for label, key, width in [('Carton','carton',8), ('Base IP','base_ip',16), ('Starting IP number','target_next',5), ('Carton limit','carton_max',4)]:
            ttk.Label(row, text=ui(label)).pack(side='left',padx=4)
            var=tk.StringVar(); self.inputs[key]=var
            ttk.Entry(row,textvariable=var,width=width).pack(side='left')
        ttk.Button(row, text=ui('Apply'), command=self.save_job).pack(side='left',padx=5)
        self.range_label=ttk.Label(row); self.range_label.pack(side='left',padx=8)
        self.grid=ttk.Frame(root,padding=(12,4)); self.grid.pack(fill='both',expand=True)
        self.status=ttk.Label(root,padding=(18,10)); self.status.pack(fill='x')
        root.bind('<Return>',lambda e:self.shortcut('ok'))
        root.bind('<Escape>',lambda e:self.shortcut('ng'))
        threading.Thread(target=self.worker,daemon=True).start()
        root.after(50,self.tick)

    def send(self, **body):
        self.commands.put(body)

    def save_job(self):
        self.send(action='job_setup',carton=self.inputs['carton'].get(),base_ip=self.inputs['base_ip'].get(),carton_max=int(self.inputs['carton_max'].get()))
        self.send(action='target_next',value=int(self.inputs['target_next'].get()))

    def start_job(self):
        try:
            self.save_job(); self.send(action='start')
        except ValueError as error: messagebox.showerror(ui('Settings'), ui(str(error)), parent=self.root)

    def worker(self):
        while not self.closed:
            try:
                while not self.commands.empty():
                    body=self.commands.get_nowait()
                    self.runtime.call('command',body)
                    self.responses.put(('command_done',body['action']))
                    if body['action']=='close': self.responses.put(('closed',None)); return
                state=self.runtime.call('state')
                images={p['index']:self.runtime.call('image',str(p['index'])) for p in state['panels'] if p['image']}
                try:self.responses.put_nowait(('state',(state,images)))
                except queue.Full:pass
            except Exception as error:
                while not self.commands.empty():
                    pending=self.commands.get_nowait()
                    if pending['action']=='close':self.commands.put(pending);break
                self.responses.put(('error',str(error)))
            threading.Event().wait(.12)

    def shortcut(self, action):
        if self.dialog or self.preview_open or not self.state: return
        focus=self.root.focus_get()
        if focus and focus.winfo_class() in ('TEntry','Entry','Text','TCombobox'): return
        p=self.state['panels'][self.state['selected']]
        if (self.state['mode']==1 or 'Appearance' in p['ok']) and p['can_'+action]: self.send(action=action,index=p['index'])

    def tick(self):
        try:
            while True:
                kind,value=self.responses.get_nowait()
                if kind=='closed': self.closed=True; self.root.destroy(); return
                if kind=='error': messagebox.showerror(ui('Error'), ui(value), parent=self.dialog or self.root)
                if kind=='command_done' and value=='settings' and self.dialog:self.dialog.destroy();self.dialog=None
                if kind=='command_done' and value==self.settings_refresh_action:self.settings_refresh_ready=True
                if kind=='state': self.render(*value)
                if kind=='preview_image':self.build_preview(*value)
        except queue.Empty: pass
        except Exception as error:
            logging.exception('Native UI update failed')
            messagebox.showerror(ui('Display error'), ui(str(error)), parent=self.root)
        if not self.closed:self.root.after(70,self.tick)

    def render(self, s, images):
        set_language(s.get('settings', {}).get('ui_language', 'ja'))
        first=self.state is None
        self.state=s
        if self.settings_refresh_action and self.settings_refresh_ready:
            busy_key={'network_scan':'network_busy','usb_scan':'usb_busy','device_scan':'discovery_busy'}[self.settings_refresh_action]
            if not s.get(busy_key):
                self.settings_refresh_action=None;self.settings_refresh_ready=False;self.root.after(0,self.settings)
        if first:
            defaults={'carton':s['settings'].get('carton','0001'),'base_ip':s['work_prefix']+'.'+str(s['scan_start']),
                      'target_next':s['settings'].get('target_next',220),'carton_max':s['settings'].get('carton_max',12)}
            for key,value in defaults.items():self.inputs[key].set(str(value))
        if s['started']:self.inputs['target_next'].set(str(s['settings'].get('target_next',220)))
        self.start.config(state='disabled' if s['started'] or s['demo'] else 'normal')
        self.counter.config(text=ui(s['counter'].split('\n')[0])); self.mode.set(str(s['mode']))
        prefix=s['settings'].get('target_prefix','192.168.0')
        self.range_label.config(text=ui(f'Range {prefix}.{s['settings'].get('target_start', 215)} ～ {prefix}.{s['settings'].get('target_end', 254)}'))
        self.status.config(text=ui(s.get('storage_error') or s['header'] + ' · ' + s['usb_status']))
        if self.last_mode!=s['mode']:
            for child in self.grid.winfo_children():child.destroy()
            self.cards=[]; self.last_mode=s['mode']
            columns=3 if s['mode']==6 else 2 if s['mode']>1 else 1
            for n in range(3):self.grid.columnconfigure(n,weight=1 if n<columns else 0)
            for n in range(2):self.grid.rowconfigure(n,weight=1 if n==0 or s['mode']>2 else 0)
            for i in range(s['mode']):
                card=ttk.LabelFrame(self.grid, text=ui(f'CAM {i + 1:02}'), padding=12, style='Idle.TLabelframe')
                card.grid(row=i//columns,column=i%columns,sticky='nsew',padx=4,pady=4)
                identity=ttk.Label(card,wraplength=380,style='Card.TLabel');identity.pack(fill='x')
                stage=ttk.Label(card,wraplength=380,style='Stage.Card.TLabel');stage.pack(fill='x',pady=(8,10))
                view=tk.Label(card, bg='#101722', fg='white', text=ui('Waiting for camera'), font=(self.font_family, 14, 'bold'));view.pack(fill='both',expand=True)
                detail=ttk.Label(card,wraplength=380,style='Card.TLabel');detail.pack(fill='x',pady=(8,4))
                buttons=ttk.Frame(card,style='Card.TFrame');buttons.pack(fill='x',pady=4)
                ok=ttk.Button(buttons,style='OK.TButton',command=lambda index=i:self.send(action='ok',index=index));ok.pack(side='left',fill='x',expand=True,padx=(0,4))
                ng=ttk.Button(buttons,style='NG.TButton',command=lambda index=i:self.send(action='ng',index=index));ng.pack(side='left',fill='x',expand=True,padx=(4,0))
                card.bind('<Button-1>',lambda e,index=i:self.send(action='select',index=index))
                view.bind('<Button-1>',lambda e,index=i:self.send(action='select',index=index))
                self.cards.append((card,identity,stage,view,detail,ok,ng))
        for p in s['panels']:
            card,identity,stage,view,detail,ok,ng=self.cards[p['index']]
            camera=p['camera']
            card_style = {'OK': 'OK', 'NG': 'NG', 'RUNING': 'Running'}.get(p.get('result'), 'Selected' if p.get('selected') else 'Idle')
            card.config(style=card_style + '.TLabelframe')
            wrap = max(200, card.winfo_width() - 32)
            for label in (identity, stage, detail): label.config(wraplength=wrap)
            identity.config(text=ui(f'SN {camera['sn']} · MAC {camera['mac']}\nIP {camera['ip']} · {model_name(camera)}' if camera else 'Disconnected'))
            stage.config(text=ui(p.get('ir_prompt') or p['status']), foreground=p.get('operation_color') or '#111827')
            detail.config(text=ui(p['detail'] + '  ' + p['fps']))
            if p['index'] in images and images[p['index']]:
                picture=Image.open(io.BytesIO(images[p['index']])).copy()
                picture.thumbnail((max(250,view.winfo_width()),max(140,view.winfo_height())))
                view.photo=ImageTk.PhotoImage(picture);view.config(image=view.photo, text=ui(''))
            else:view.config(image='', text=ui(p['message']), fg=p['color'] or 'white')
            ok.config(text=ui(p['ok']), state='normal' if p['can_ok'] else 'disabled')
            if p.get('hide_ok'):ok.pack_forget()
            elif not ok.winfo_manager():ok.pack(side='left',fill='x',expand=True,before=ng)
            ng.config(text=ui(p['ng']), state='normal' if p['can_ng'] else 'disabled')
        if s['recovery_pending'] and not self.dialog and not self.notice_handled:
            self.notice_handled=True
            clear=messagebox.askyesno(ui('Previous job'), ui('Clear the displayed OK/NG counters?\nNo only dismisses this notice.'), parent=self.root)
            self.send(action='clear_counters' if clear else 'startup_continue')
            self.state['recovery_pending']=False
        if not s.get('preview'):self.preview_waiting_clear=False
        if s.get('preview') and not self.preview_open and not self.preview_waiting_clear:self.preview(s['preview'])
        localize_widgets(self.root)

    def settings(self):
        if not self.state or self.dialog:return
        s=self.state;settings=s['settings']; window=tk.Toplevel(self.root);self.dialog=window
        window.title(ui('Inspection settings'));window.geometry('820x700')
        notebook=ttk.Notebook(window);notebook.pack(fill='both',expand=True,padx=10,pady=10)
        tabs={name:ttk.Frame(notebook,padding=12) for name in ('Connect','IP allocation','Allowed models','Authentication','Display','GAS reporting')}
        for name,frame in tabs.items():notebook.add(frame, text=ui(name))
        variables={}
        def field(tab,key,label,value,secret=False):
            frame=ttk.Frame(tabs[tab]);frame.pack(fill='x',pady=5)
            ttk.Label(frame, text=ui(label), width=23).pack(side='left')
            var=tk.StringVar(value=str(value));variables[key]=var
            entry=ttk.Entry(frame,textvariable=var,show='*' if secret else '');entry.pack(side='left',fill='x',expand=True)
            if secret:ttk.Button(frame, text=ui('Display'), command=lambda: entry.config(show='' if entry.cget('show') else '*')).pack(side='left')
        nics=settings.get('network_interfaces',[])
        nic_labels=[x['name']+' / '+x['mac']+' / '+(', '.join(a['ip'] for a in x.get('ipv4',[])) or x.get('ip','Not configured')) for x in nics]
        nic=ttk.Combobox(tabs['Connect'],values=nic_labels,state='readonly');nic.pack(fill='x')
        if settings.get('network_id') in [x['id'] for x in nics]:nic.current([x['id'] for x in nics].index(settings['network_id']))
        ttk.Label(tabs['Connect'], text=ui('IP is reference information. L2 discovery works across IP subnets.')).pack(anchor='w',pady=8)
        ttk.Button(tabs['Connect'], text=ui('Rescan network adapters'), command=lambda: self.refresh_settings('network_scan')).pack(anchor='w')
        usbs=list(s['usb_devices'].items());usb=ttk.Combobox(tabs['Connect'],values=['Automatic']+[str(k)+': '+v for k,v in usbs],state='readonly');usb.pack(fill='x',pady=12);usb.current(0)
        for index,(key,_) in enumerate(usbs):
            if s['usb_index'] is not None and int(key)==int(s['usb_index']):usb.current(index+1)
        ttk.Button(tabs['Connect'], text=ui('Rescan USB cameras'), command=lambda: self.refresh_settings('usb_scan')).pack(anchor='w')
        fields=[('work_prefix','Work IP prefix',''),('work_start','Work IP start',150),('work_end','Work IP end',151),
            ('target_prefix','Target IP prefix','192.168.0'),('target_start','Target IP start',215),('target_end','Target IP end',254),
            ('target_next','Next target IP number',220),('target_mask','Subnet mask',''),('target_gateway','Gateway','')]
        for key,label,default in fields:field('IP allocation',key,label,settings.get(key,default))
        models={d.get('oem_model') or d.get('model'):d for d in s['devices'] if d.get('model') or d.get('oem_model')};model_vars={}
        for value in settings.get('allowed_models',[]):
            if not any(value in (d.get('model'),d.get('oem_model')) for d in models.values()):models[value]={'model':value}
        for value,device in models.items():
            var=tk.BooleanVar(value=value in settings.get('allowed_models',[]) or device.get('model') in settings.get('allowed_models',[]));model_vars[value]=var
            ttk.Checkbutton(tabs['Allowed models'], text=ui(model_name(device)), variable=var).pack(anchor='w')
        ttk.Button(tabs['Allowed models'], text=ui('Discover device information'), command=lambda: self.refresh_settings('device_scan')).pack(anchor='w',pady=6)
        tree=ttk.Treeview(tabs['Allowed models'],columns=('mac','ip','model'),show='headings',height=10)
        for column,label,width in [('mac','MAC',200),('ip','IP',130),('model','Model',290)]:tree.heading(column, text=ui(label));tree.column(column,width=width)
        tree.pack(fill='both',expand=True)
        for device in s['devices']:tree.insert('', 'end',values=(device['mac'],device.get('ip',''),model_name(device)))
        def unlock_selected():
            if tree.selection():self.unlock(tree.item(tree.selection()[0],'values')[0])
        ttk.Button(tabs['Allowed models'], text=ui('Release selected MAC'), command=unlock_selected).pack(anchor='w',pady=6)
        for key,label,default,secret in [('access_username','Before config: username','admin',False),('access_password','Before config: password','',True),
                ('after_config_username','After config: username','admin',False),('after_config_password','After config: password','',True),('config_import_password','Config import password','',True)]:
            field('Authentication',key,label,settings.get(key,default),secret)
        field('Display','mode','Panel count (1/2/4/6)',s['mode']);field('Display','stream','RTSP /sub or /main',s['stream'])
        language_row=ttk.Frame(tabs['Display']);language_row.pack(fill='x',pady=8)
        ttk.Label(language_row,text=ui('Language'),width=23).pack(side='left')
        language=ttk.Combobox(language_row,values=['English',ui('Japanese')],state='readonly')
        language.pack(side='left',fill='x',expand=True)
        language.current(0 if settings.get('ui_language','ja')=='en' else 1)
        field('GAS reporting','post_url','POST URL',settings.get('post_url',''))
        enabled=tk.BooleanVar(value=bool(settings.get('post_enabled')));ttk.Checkbutton(tabs['GAS reporting'], text=ui('Automatically report completed records'), variable=enabled).pack(anchor='w')
        for text,action in [('Test connection','post_test'),('Retry unsent records','post_retry')]:ttk.Button(tabs['GAS reporting'], text=ui(text), command=lambda a=action: self.send(action=a)).pack(side='left',padx=4)
        post_text=scrolledtext.ScrolledText(tabs['GAS reporting'],height=12);post_text.pack(fill='both',expand=True,pady=12)
        post_text.insert('1.0',json.dumps(s.get('post',{}),ensure_ascii=False,indent=2)+'\nResponse example: {"ok":true,"record_id":"same ID as request"}')
        def update_post():
            if not window.winfo_exists():return
            post_text.delete('1.0','end');post_text.insert('1.0',json.dumps((self.state or {}).get('post',{}),ensure_ascii=False,indent=2)+'\nResponse example: {"ok":true,"record_id":"same ID as request"}')
            window.after(700,update_post)
        window.after(700,update_post)
        def example():
            viewer=tk.Toplevel(window);viewer.title(ui('GAS receiver example'));text=scrolledtext.ScrolledText(viewer,width=100,height=30);text.pack(fill='both',expand=True)
            text.insert('1.0',(APP_ROOT/'examples/gas_receiver.gs').read_text(encoding='utf-8'))
        ttk.Button(tabs['GAS reporting'], text=ui('GAS receiver code example'), command=example).pack(anchor='w')
        def save():
            try:
                values={key:var.get() for key,var in variables.items()}
                for key in ('work_start','work_end','target_start','target_end','target_next','mode'):values[key]=int(values[key])
                if nic.current()<0:raise ValueError('Select a network adapter')
                values.update(action='settings',network=nics[nic.current()]['id'],usb=int(usbs[usb.current()-1][0]) if usb.current()>0 else None,
                    start=values['work_start'],end=values['work_end'],post_enabled=enabled.get(),allowed_models=[m for m,v in model_vars.items() if v.get()])
                values['ui_language']='en' if language.current()==0 else 'ja'
                self.send(**values)
            except ValueError as error:messagebox.showerror(ui('Settings'), ui(str(error)), parent=window)
        def close():self.dialog=None;window.destroy()
        window.protocol('WM_DELETE_WINDOW',close)
        ttk.Button(window, text=ui('Save and apply'), command=save).pack(pady=8)

    def refresh_settings(self, action):
        if self.dialog:self.dialog.destroy();self.dialog=None
        self.settings_refresh_action=action;self.settings_refresh_ready=False
        self.send(action=action)

    def unlock(self, mac):
        if messagebox.askyesno(ui('Release'), ui(mac + "Clear this camera's kitting cache. Keep its history."), parent=self.dialog):self.send(action='rekit_device',mac=mac)

    def logs(self):
        window=tk.Toplevel(self.root);window.title(ui('Application log'));window.geometry('950x650')
        text=scrolledtext.ScrolledText(window);text.pack(fill='both',expand=True)
        self.send(action='debug',enabled=True)
        def update():
            if not window.winfo_exists():return
            path=APP_ROOT/'data/logs/tool.log'
            lines=path.read_text(encoding='utf-8',errors='replace').splitlines()[-250:] if path.exists() else []
            text.delete('1.0','end');text.insert('1.0','\n'.join(lines));text.see('end');window.after(1000,update)
        update()

    def preview(self, info):
        self.preview_open=True;window=tk.Toplevel(self.root);window.title(ui('Appearance NG - mark the affected area'))
        def fetch():
            try:data=self.runtime.call('image','preview');self.responses.put(('preview_image',(window,data,info)))
            except Exception as error:self.responses.put(('error',str(error)))
        # Snapshot is already captured; the image request contains no camera operation.
        threading.Thread(target=fetch,daemon=True).start()
        def cancel():self.send(action='cancel_ng');self.preview_open=False;self.preview_waiting_clear=True;window.destroy()
        window.protocol('WM_DELETE_WINDOW',cancel)

    def build_preview(self, window, data, info):
        if not window.winfo_exists():return
        original=Image.open(io.BytesIO(data));image=original.copy();image.thumbnail((950,580))
        photo=ImageTk.PhotoImage(image);canvas=tk.Canvas(window,width=image.width,height=image.height);canvas.pack();canvas.photo=photo;canvas.create_image(0,0,anchor='nw',image=photo)
        boxes=[];drag=[];scale=original.width/image.width
        def down(event):drag[:]=[event.x,event.y];drag.append(canvas.create_rectangle(event.x,event.y,event.x,event.y,outline='red',width=2))
        def move(event):
            if drag:canvas.coords(drag[2],drag[0],drag[1],event.x,event.y)
        def up(event):
            if drag:
                x1,x2=sorted((drag[0],event.x));y1,y2=sorted((drag[1],event.y));boxes.append([int(x1*scale),int(y1*scale),int(x2*scale),int(y2*scale)]);drag.clear()
        canvas.bind('<Button-1>',down);canvas.bind('<B1-Motion>',move);canvas.bind('<ButtonRelease-1>',up)
        category=ttk.Combobox(window,values=[ui('Appearance defect'),ui('Damage')],state='readonly')
        category.current(0);category.pack(fill='x')
        def save():
            if not boxes:messagebox.showerror(ui('NG'), ui('Mark the affected area'), parent=window);return
            self.send(action='save_ng',category='B' if category.current()==0 else 'D',boxes=boxes)
            self.preview_open=False;self.preview_waiting_clear=True;window.destroy()
        ttk.Button(window, text=ui('Save and confirm NG'), command=save).pack(pady=8)
        def cancel():self.send(action='cancel_ng');self.preview_open=False;self.preview_waiting_clear=True;window.destroy()
        ttk.Button(window, text=ui('Cancel'), command=cancel).pack(pady=4)

    def close(self):
        self.send(action='close')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--demo',action='store_true');parser.add_argument('--self-test',action='store_true');args=parser.parse_args()
    if args.self_test:
        from unittest.mock import Mock
        panel={'index':0,'camera':None,'status':'Idle','message':'Idle','detail':'','fps':'','color':'white','image':False,'ok':'OK','ng':'NG','can_ok':False,'can_ng':False,'result':None}
        state={'mode':1,'selected':0,'started':False,'demo':True,'settings':{},'work_prefix':'192.168.0','scan_start':150,'counter':'OK 0 · NG 0','header':'Idle','usb_status':'Disconnected','usb_index':None,'usb_devices':{},'panels':[panel],'recovery_pending':False,'devices':[],'stream':'/sub','post':{}}
        root=tk.Tk();root.withdraw();runtime=Mock();runtime.call.return_value=state
        app=Desktop(root,runtime)
        for language in ('en','ja'):
            state['settings']['ui_language']=language
            for count in (1,2,4,6):
                state['mode']=count;state['panels']=[dict(panel,index=index) for index in range(count)]
                app.render(state,{});root.update_idletasks();assert len(app.cards)==count
                assert app.start.cget('text')==ui('Start inspection')
            app.settings();app.dialog.withdraw();root.update_idletasks()
            app.dialog.destroy();app.dialog=None
        app.closed=True;root.destroy();print('Native window widgets/settings OK; no HTTP server or hardware operations');return
    logs=APP_ROOT/'data/logs';logs.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[RotatingFileHandler(logs/'tool.log',maxBytes=5_000_000,backupCount=5,encoding='utf-8')])
    with InstanceLock(APP_ROOT/'data/application.lock'):
        root=tk.Tk();app=Desktop(root,Runtime(args.demo))
        marker=APP_ROOT/'data/native_ready';marker.write_text('ready',encoding='utf-8')
        try:root.mainloop()
        finally:marker.unlink(missing_ok=True)


if __name__=='__main__':main()
