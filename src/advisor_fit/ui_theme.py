"""Approved ink-and-paper visual language for the Streamlit workflow."""

CSS = """
<style>
:root { --ink:#1d2028; --paper:#f5f2eb; --surface:#fffcf7; --line:#dedbd3;
        --purple:#7569af; --gold:#b48a52; --muted:#777975; }
html, body, [data-testid="stAppViewContainer"] { background:var(--paper); color:#292b31; }
[data-testid="stAppViewContainer"] > .main { background:var(--paper); }
[data-testid="stHeader"] { background:var(--paper); border-bottom:1px solid var(--line); }
[data-testid="stHeader"] [data-testid="stToolbar"] { background:transparent; }
.block-container { max-width:1440px; padding:3.2rem 3.2rem 6rem; }
h1,h2,h3 { font-family:"Noto Serif SC","Songti SC",SimSun,Georgia,serif!important;
  font-weight:400!important; color:#282a33; }
h1 { font-size:2.1rem!important; letter-spacing:.02em; line-height:1.4; }
h2 { font-size:1.42rem!important; margin-top:1.4rem; }
h3 { font-size:1.12rem!important; }
p,li,[data-testid="stMarkdownContainer"] p { color:#52545b; line-height:1.78; }
[data-testid="stCaptionContainer"],.stCaption { color:#81817e; font-size:.78rem; }
[data-testid="stSidebar"] { background:var(--ink); border-right:1px solid #35343c;
  min-width:244px; }
[data-testid="stSidebar"] * { color:#dad9df; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color:#abaab4; }
[data-testid="stSidebar"] [data-testid="stElementContainer"]:has([data-testid="stButton"]),
[data-testid="stSidebar"] [data-testid="stButton"] { width:100%; }
[data-testid="stSidebar"] [data-testid="stButton"] button { width:100%; justify-content:flex-start;
  color:#d5d2df; background:transparent; border:1px solid transparent; border-radius:6px;
  font-size:.83rem; padding:.6rem .8rem; }
[data-testid="stSidebar"] [data-testid="stButton"] button > div { width:100%; text-align:left; }
[data-testid="stSidebar"] [data-testid="stButton"] button > div > span {
  width:100%; justify-content:flex-start!important; }
[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
  background:#34323d; border-color:#49434f; color:#fff; }
[data-testid="stSidebar"] .st-key-nav_home button:focus-visible,
[data-testid="stSidebar"] .st-key-nav_resume button:focus-visible,
[data-testid="stSidebar"] .st-key-nav_professor button:focus-visible,
[data-testid="stSidebar"] .st-key-nav_papers button:focus-visible,
[data-testid="stSidebar"] .st-key-nav_report button:focus-visible,
[data-testid="stSidebar"] .st-key-nav_email button:focus-visible,
[data-testid="stSidebar"] .st-key-nav_history button:focus-visible {
  outline:2px solid #b9a8de; outline-offset:1px; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:.16rem; }
.studio-brand { font:600 1.45rem "Noto Serif SC","Songti SC",SimSun,serif; letter-spacing:.09em;
  color:#f3f0ed; padding:14px 6px 24px; }
.studio-brand .brand-mark { color:#c1acdb; margin-right:9px; }
.studio-brand small { display:block; font:500 .55rem Arial,sans-serif; letter-spacing:.22em;
  color:#a9a3b8; margin:4px 0 0 31px; }
.side-label { font:.61rem Arial,sans-serif; letter-spacing:.18em; color:#8e8c9d;
  padding:10px 8px; margin:8px 0 10px; }
.side-note { border:1px solid #45434f; border-radius:7px; color:#c4c0ca;
  background:#282932; font-size:.73rem; line-height:1.7; padding:14px; margin-top:26px; }
.side-note b { color:#decaa5; font-weight:500; }
.page-eyebrow { font:.67rem Arial,sans-serif; font-weight:700; letter-spacing:.2em;
  color:var(--purple); margin-bottom:12px; }
.page-intro { color:#777975; font-size:.86rem; margin:-4px 0 1.7rem; }
.section-note { color:#847f78; font:.7rem Arial,sans-serif; letter-spacing:.14em;
  border-bottom:1px solid var(--line); padding:0 0 10px; margin:18px 0 16px; }
.step-strip { display:flex; background:#fffcf8; border:1px solid var(--line); margin:20px 0 30px; }
.step-strip span { flex:1; font-size:.79rem; color:#a2a09b; padding:15px 18px;
  border-right:1px solid var(--line); }
.step-strip span:last-child { border:0; }
.step-strip .current { background:#eeeaf5; color:#605483; }
.step-strip .done { color:#54806f; }
.step-strip b { font:1.03rem Georgia,serif; font-weight:400; margin-right:8px; }
.stButton>button,.stDownloadButton>button { border-radius:5px; border:1px solid var(--line);
  background:#fffcf8; color:#44464d; box-shadow:none; font-weight:500; }
.stButton>button:hover,.stDownloadButton>button:hover { color:#65558f; border-color:#a999cb; }
.stButton>button[kind="primary"] { background:var(--ink); color:white; border-color:var(--ink); }
.stButton>button[kind="primary"]:hover { background:#373440; color:white; border-color:#373440; }
input,textarea,[data-testid="stSelectbox"] > div > div { border-radius:4px!important; }
input:focus,textarea:focus { border-color:var(--purple)!important;
  box-shadow:0 0 0 2px #7569af22!important; }
div[data-testid="stExpander"],div[data-testid="stVerticalBlockBorderWrapper"] { background:#fffdf9;
  border-color:var(--line); border-radius:6px; box-shadow:none; }
div[data-testid="stFileUploader"] { background:#fffdf9; border:1px solid var(--line);
  padding:18px; border-radius:6px; }
.stTabs [data-baseweb="tab-list"] { border-bottom:1px solid var(--line); gap:0; }
.stTabs [data-baseweb="tab"] { color:#82807f; border-radius:0; padding:.8rem 1.2rem; }
.stTabs [aria-selected="true"] { color:var(--purple); border-bottom:2px solid var(--purple); }
.stAlert { border-radius:5px; }
.landing-hero { background:#202129; color:#f7f4ee; border-radius:7px; padding:70px 7%;
  position:relative; overflow:hidden; min-height:440px; margin-top:5px; }
.landing-hero:after { content:""; position:absolute; right:-80px; top:-145px;
  width:540px; height:540px; border-radius:50%; border:1px solid #80768a70;
  box-shadow:0 0 0 65px #ffffff05,0 0 0 135px #c4a8850a; }
.landing-hero .label { color:#c9b4e7; letter-spacing:.2em; font:700 .65rem Arial,sans-serif; }
.landing-hero h1 { position:relative; z-index:1; color:#f7f4ee; font-size:clamp(2.6rem,4vw,4.4rem);
  line-height:1.3; font-weight:400; margin:25px 0 20px; }
.landing-hero em { color:#d6c390; font-style:normal; }
.landing-hero p { position:relative; z-index:1; max-width:590px; color:#c5c1c8;
  line-height:2; font-size:.9rem; }
.landing-hero .orb { position:absolute; z-index:1; right:12%; top:26%; width:210px; height:210px;
  border-radius:50%; background:radial-gradient(circle at 30% 25%,#77718b,#353542 68%);
  border:1px solid #777083; box-shadow:0 40px 70px #15151d; display:grid; place-items:center;
  font:2rem "Songti SC",serif; letter-spacing:.15em; color:#f3e7d5; }
.landing-path { padding:25px; background:#fffcf8; border:1px solid var(--line);
  margin-top:18px; color:#695f6d; font-size:.84rem; }
.landing-path b { color:var(--gold); font-family:Georgia,serif; margin:0 7px 0 16px; }
.profile-band { background:#252630; color:#f7f3ec; border-radius:7px; padding:25px 30px;
  margin:4px 0 24px; position:relative; overflow:hidden; }
.profile-band:after { content:""; width:310px; height:310px; border-radius:50%;
  border:1px solid #887d92; position:absolute; right:11%; top:-250px;
  box-shadow:0 0 0 70px #ffffff06; }
.profile-band .label { font:700 .62rem Arial,sans-serif; color:#c7b3e2; letter-spacing:.19em; }
.profile-band h2 { color:#faf3ec; margin:10px 0 6px; font-size:2rem; }
.profile-band p { color:#bdb9c3; margin:0; font-size:.8rem; }
.folio { font:700 .65rem Arial,sans-serif; letter-spacing:.2em; color:#9c8c78;
  border-bottom:1px solid #ded3c3; padding-bottom:19px; }
.brief-verdict { border-top:2px solid #bc9a68; background:#f5f0e9;
  padding:15px 18px; display:flex; gap:20px; align-items:center; margin:12px 0 18px; }
.brief-verdict strong { font:500 1rem "Noto Serif SC",SimSun,serif; color:#36333b; }
.brief-verdict span { font-size:.81rem; color:#67635d; }
@media(max-width:900px) { .block-container { padding:2rem 1.2rem 4rem; }
  .landing-hero .orb { opacity:.25; right:2%; } }
</style>
"""
