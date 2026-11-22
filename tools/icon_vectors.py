"""24-unit source geometry for the application icon set."""
def draw_vector_icon(c, name, x, y, color, size=20):
    s = size/24
    def line(*pts):
        c.create_line(*[v*s+(x if i%2 == 0 else y) for i,v in enumerate(pts)],
                      fill=color, width=1.7, capstyle="round", joinstyle="round")
    def box(a,b,d,e):
        c.create_rectangle(x+a*s,y+b*s,x+d*s,y+e*s,outline=color,width=1.5)
    def oval(a,b,d,e):
        c.create_oval(x+a*s,y+b*s,x+d*s,y+e*s,outline=color,width=1.6)
    if name == "home":
        for a,b in ((3,3),(14,3),(3,14),(14,14)): box(a,b,a+7,b+7)
    elif name == "check":
        oval(2,2,22,22); line(7,12,10.5,15.5,17,8.5)
    elif name == "learn":
        line(2,8,12,3,22,8,12,13,2,8); line(6,11,6,17,12,20,18,17,18,11)
    elif name == "templates":
        box(5,3,20,21); line(5,7,2,7,2,23,17,23); line(9,8,16,8); line(9,12,16,12); line(9,16,14,16)
    elif name == "context":
        oval(8,8,16,16)
        for a,b in ((3,3),(19,3),(3,19),(19,19)):
            oval(a-2,b-2,a+2,b+2); line(a,b,12,12)
    elif name == "history":
        c.create_arc(x+3*s,y+3*s,x+21*s,y+21*s,start=35,extent=-310,style="arc",outline=color,width=1.7)
        line(3,3,3,9,9,9); line(12,7,12,13,16,15)
    elif name == "settings":
        for a,b in ((6,8),(12,16),(18,10)):
            line(a,3,a,b-3); line(a,b+3,a,21); oval(a-2.7,b-2.7,a+2.7,b+2.7)
    elif name == "diagnostics":
        line(2,13,6,13,9,5,14,20,17,10,20,13,23,13)
    elif name == "search":
        oval(3,3,16,16); line(15,15,22,22)
    elif name == "arrow":
        line(5,12,20,12); line(14,6,20,12,14,18)
    elif name == "changes":
        box(4,3,20,21); line(8,8,16,8); line(8,12,16,12); line(8,16,13,16)
    elif name == "removals":
        line(12,2,21,5,20,13,17,18,12,22,7,18,4,13,3,5,12,2)
        line(8,11,16,11)
    elif name == "spark":
        line(12,2,15,9,22,12,15,15,12,22,9,15,2,12,9,9,12,2)
    elif name == "repunish":
        line(9.5,5.5,14.5,0.8+0.7,21.5,8.5,16.5,13.5,9.5,5.5); line(13,10,4,19); line(11,22,21,22)
    elif name == "send":
        line(3,11,21,3,14,21,11,13,3,11); line(11,13,21,3)
    elif name == "pause":
        line(9,5,9,19); line(15,5,15,19)
    elif name == "play":
        line(7,4,19,12,7,20,7,4)
    elif name == "trash":
        line(4,6.5,20,6.5); line(9.5,6.5,9.5,3.5,14.5,3.5,14.5,6.5); line(6.5,6.5,7.5,20.5,16.5,20.5,17.5,6.5)
        line(10.3,10.5,10.6,17); line(13.7,10.5,13.4,17)
    elif name == "broom":
        line(19.5,3,13,11); line(8.5,11,15.5,11,17.5,20.5,4,20.5,8.5,11); line(9,15,8,20.5); line(12.5,15,12.5,20.5)
    elif name == "check_all":
        box(3.5,3.5,20.5,20.5); line(7.5,12,10.7,15.2,16.5,8.8)
    elif name == "uncheck":
        box(3.5,3.5,20.5,20.5)
    elif name == "invert":
        box(3.5,3.5,20.5,20.5); line(3.5,20.5,20.5,3.5); line(12,3.5,3.5,12)
    elif name == "toggle_on":
        box(2.5,6.5,21.5,17.5); oval(13,8.5,20,15.5)
    elif name == "toggle_off":
        box(2.5,6.5,21.5,17.5); oval(4,8.5,11,15.5)
    elif name == "plus":
        line(12,5,12,19); line(5,12,19,12)
    elif name == "chevdown":
        line(6.5,9.5,12,15,17.5,9.5)
    elif name == "chevup":
        line(6.5,14.5,12,9,17.5,14.5)
    elif name == "chevleft":
        line(14.5,6.5,9,12,14.5,17.5)
    elif name == "chevright":
        line(9.5,6.5,15,12,9.5,17.5)
    elif name == "info":
        oval(2.5,2.5,21.5,21.5); line(12,11,12,17); line(12,7.4,12,7.6)
    elif name == "warning":
        line(12,3,22,20.5,2,20.5,12,3); line(12,9.5,12,14.5); line(12,17.4,12,17.6)
    elif name == "error":
        oval(2.5,2.5,21.5,21.5); line(8.5,8.5,15.5,15.5); line(15.5,8.5,8.5,15.5)
    elif name == "question":
        oval(2.5,2.5,21.5,21.5); line(9.3,9.3,10.5,7.4,13.5,7.2,15,9,14.4,11.2,12,12.8,12,14.4); line(12,17.4,12,17.6)
    elif name == "key":
        oval(3,9,11,17); line(11,13,21,13); line(17,13,17,16.5); line(20,13,20,15.5)
    elif name == "external":
        line(13.5,4,20,4,20,10.5); line(20,4,11,13); line(17,14,17,20,4,20,4,7,10,7)
    elif name == "copy":
        box(8.5,8.5,20,20); line(15.5,8.5,15.5,4,4,4,4,15.5,8.5,15.5)
    elif name == "download":
        line(12,3.5,12,15); line(7,10,12,15,17,10); line(4.5,20,19.5,20)
    elif name == "upload":
        line(12,15,12,3.5); line(7,8.5,12,3.5,17,8.5); line(4.5,20,19.5,20)
    elif name == "refresh":
        c.create_arc(x+4*s,y+4*s,x+20*s,y+20*s,start=40,extent=290,style="arc",outline=color,width=1.7)
        line(18.2,2.8,18.2,7.1,13.9,7.1)
    elif name == "save":
        box(4,4,20,20); line(8,4,8,9,15,9,15,4); line(8,20,8,14,16,14,16,20)
    elif name == "stop":
        box(6.5,6.5,17.5,17.5)
    elif name == "folder":
        line(3,6,9,6,11,8.5,21,8.5,21,19,3,19,3,6)
    elif name == "file":
        line(6,3,14,3,19,8,19,21,6,21,6,3); line(14,3,14,8,19,8); line(9,13,16,13); line(9,17,14,17)
    elif name == "lock":
        box(5,11,19,21)
        c.create_arc(x+8*s,y+3.5*s,x+16*s,y+11.5*s,start=0,extent=180,style="arc",outline=color,width=1.7)
        line(8,7.5,8,11); line(16,7.5,16,11); line(12,14.5,12,17.5)
    elif name == "unlock":
        box(5,11,19,21)
        c.create_arc(x+8*s,y+1.5*s,x+16*s,y+9.5*s,start=0,extent=180,style="arc",outline=color,width=1.7)
        line(8,5.5,8,11); line(16,5.5,16,6.5); line(12,14.5,12,17.5)
    elif name == "palette":
        oval(2.5,2.5,21.5,21.5); oval(7,7,9.6,9.6); oval(11,5.5,13.6,8.1); oval(15,8.5,17.6,11.1); line(12,21.5,13.5,17,17,16,21.5,14)
    elif name == "prio_low":
        oval(2.5,2.5,21.5,21.5); line(12,7.5,12,16); line(8.3,12.6,12,16.3,15.7,12.6)
    elif name == "flag":
        line(5,21,5,3.5); line(5,4,18,4,15,8.5,18,13,5,13)
    elif name == "power":
        c.create_arc(x+4*s,y+4*s,x+20*s,y+20*s,start=125,extent=290,style="arc",outline=color,width=1.7)
        line(12,2.5,12,11)
