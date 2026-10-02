import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
import io
import zipfile
import os

st.set_page_config(
    page_title="Sistem BPKB Terpadu",
    page_icon="✍️",
    layout="wide"
)

# ==========================================
# KONVERSI TANGGAL & NORMALISASI
# ==========================================

INDONESIAN_MONTHS = {
    'JANUARI': 1, 'JAN': 1,
    'FEBRUARI': 2, 'FEB': 2,
    'MARET': 3, 'MAR': 3,
    'APRIL': 4, 'APR': 4,
    'MEI': 5,
    'JUNI': 6, 'JUN': 6,
    'JULI': 7, 'JUL': 7,
    'AGUSTUS': 8, 'AGU': 8, 'AGT': 8,
    'SEPTEMBER': 9, 'SEP': 9,
    'OKTOBER': 10, 'OKT': 10,
    'NOVEMBER': 11, 'NOV': 11,
    'DESEMBER': 12, 'DES': 12
}

def normalize_text(text):
    if not isinstance(text, str):
        return ""
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())

def parse_date_standard(date_input):
    if pd.isna(date_input) or not date_input or str(date_input).strip() in ['', 'nan', 'None']:
        return ""
    
    date_str = str(date_input).strip()
    
    # 1. Format Teks PDF (misal: "18 September 2026")
    match_txt = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", date_str)
    if match_txt:
        day = int(match_txt.group(1))
        month_str = match_txt.group(2).upper()
        year = int(match_txt.group(3))
        if month_str in INDONESIAN_MONTHS:
            month = INDONESIAN_MONTHS[month_str]
            return f"{year:04d}-{month:02d}-{day:02d}"

    # 2. Format Numerik DD/MM/YYYY atau MM/DD/YYYY
    match_num = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})", date_str)
    if match_num:
        n1 = int(match_num.group(1))
        n2 = int(match_num.group(2))
        year = int(match_num.group(3))
        
        if n1 > 12:
            day, month = n1, n2
        elif n2 > 12:
            day, month = n2, n1
        else:
            day, month = n1, n2
            
        return f"{year:04d}-{month:02d}-{day:02d}"
            
    try:
        dt = pd.to_datetime(date_str, errors='coerce', dayfirst=True)
        if pd.notna(dt):
            return dt.strftime("%Y-%m-%d")
    except:
        pass
        
    return date_str

def standardize_db_columns(df):
    column_mapping = {}
    assigned = set()
    
    for col in df.columns:
        clean_col = normalize_text(col)
        
        if "balai_lelang" not in assigned and any(keyword in clean_col for keyword in ["BALAI", "LELANG", "DISERAHKAN", "PENERIMA"]):
            column_mapping[col] = "balai_lelang"
            assigned.add("balai_lelang")
        # Ditambahkan syarat "SELLING" not in clean_col agar Tanggal Selling tidak ditarik sebagai Tanggal Pengajuan
        elif "tgl_pengajuan" not in assigned and any(keyword in clean_col for keyword in ["TGL", "TANGGAL", "PENGAJUAN", "DATE"]) and "SELLING" not in clean_col:
            column_mapping[col] = "tgl_pengajuan"
            assigned.add("tgl_pengajuan")
        elif "nopol" not in assigned and any(keyword in clean_col for keyword in ["NOPOL", "POLISI", "PLAT", "VEHICLE", "NOKENDARAAN"]):
            column_mapping[col] = "nopol"
            assigned.add("nopol")
            
    df_renamed = df.rename(columns=column_mapping)
    
    cols = list(df_renamed.columns)
    if not {'balai_lelang', 'tgl_pengajuan', 'nopol'}.issubset(set(df_renamed.columns)) and len(cols) >= 3:
        df_renamed = df_renamed.rename(columns={
            cols[0]: 'nopol',
            cols[1]: 'balai_lelang',
            cols[2]: 'tgl_pengajuan'
        })
        
    return df_renamed

def load_gsheet_data(gsheet_url, sheet_name=None):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", gsheet_url)
    if not match:
        raise ValueError("URL Google Sheet tidak valid.")
    
    spreadsheet_id = match.group(1)
    csv_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv"
    if sheet_name:
        csv_url += f"&sheet={sheet_name}"
        
    df = pd.read_csv(csv_url)
    return df

# ==========================================
# EKSTRAKSI PDF MULTI-PAGE
# ==========================================

def extract_pdf_data(pdf_bytes, filename):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    extracted_nopols = set()
    
    nopol_pattern = r'\b[A-Z]{1,2}[-\s]*\d{1,4}[-\s]*[A-Z]{1,3}\b'

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text()
        full_text += page_text + "\n"
        
        cleaned_lines = []
        for line in page_text.split('\n'):
            cleaned_tokens = [tok for tok in line.split() if '/' not in tok]
            cleaned_lines.append(" ".join(cleaned_tokens))
            
        cleaned_page_text = "\n".join(cleaned_lines)
        
        matches = re.findall(nopol_pattern, cleaned_page_text)
        for m in matches:
            norm = normalize_text(m)
            if re.match(r'^[A-Z]{1,2}\d{1,4}[A-Z]{1,3}$', norm):
                if not norm.startswith(("FRM", "AST", "TERLAMPIR")):
                    extracted_nopols.add(norm)

    tgl_match = re.search(
        r"Tanggal\s+Pengajuan[\s\:\n\-]+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", 
        full_text, 
        re.IGNORECASE
    )
    tgl_raw = tgl_match.group(1).strip() if tgl_match else ""
    tgl_std = parse_date_standard(tgl_raw)
    
    balai_lelang = "Tidak Ditemukan"
    known_balai = ["JBA", "IBID", "MAS", "CARE", "BALINDO"]
    for kb in known_balai:
        if re.search(r'\b' + kb + r'\b', full_text, re.IGNORECASE):
            balai_lelang = kb
            break
            
    if balai_lelang == "Tidak Ditemukan":
        balai_match = re.search(
            r"Diserahkan\s+kepada[\s\:\n\-]+([A-Za-z0-9]+)", 
            full_text, 
            re.IGNORECASE
        )
        if balai_match:
            balai_lelang = balai_match.group(1).strip().replace(".", "")
    
    return {
        "filename": filename,
        "tgl_raw": tgl_raw,
        "tgl_std": tgl_std,
        "balai_lelang": balai_lelang,
        "nopols": extracted_nopols,
        "doc": doc
    }

# ==========================================
# VALIDASI HANYA NOPOL YANG ADA DI PDF
# ==========================================

def validate_data_detailed(pdf_data, db_df):
    balai_pdf = pdf_data["balai_lelang"].upper()
    tgl_pdf_std = pdf_data["tgl_std"]
    tgl_pdf_raw = pdf_data["tgl_raw"]
    pdf_nopols = pdf_data["nopols"]
    
    if not pdf_nopols:
        return False, "Tidak ada No. Polisi yang terdeteksi di dalam file PDF.", pd.DataFrame()

    db_df['norm_nopol'] = db_df['nopol'].apply(normalize_text)
    db_df['tgl_std'] = db_df['tgl_pengajuan'].apply(parse_date_standard)
    
    balai_col = db_df['balai_lelang'].iloc[:, 0] if isinstance(db_df['balai_lelang'], pd.DataFrame) else db_df['balai_lelang']
    db_df['balai_upper'] = balai_col.astype(str).str.upper()
    
    nopol_details = []
    is_fully_valid = True
    
    for nopol in sorted(pdf_nopols):
        matches = db_df[db_df['norm_nopol'] == nopol]
        
        if matches.empty:
            is_fully_valid = False
            nopol_details.append({
                "Nopol": nopol,
                "Status": "❌ Salah",
                "Detail Alasan": "Nopol tidak terdaftar sama sekali di Database"
            })
        else:
            exact_match = matches[
                (matches['balai_upper'] == balai_pdf) & 
                (matches['tgl_std'] == tgl_pdf_std)
            ]
            
            if not exact_match.empty:
                nopol_details.append({
                    "Nopol": nopol,
                    "Status": "✅ Benar",
                    "Detail Alasan": "Sesuai (Nopol, Balai Lelang & Tgl Pengajuan Cocok)"
                })
            else:
                is_fully_valid = False
                reasons = []
                
                for _, row in matches.iterrows():
                    if row['balai_upper'] != balai_pdf:
                        reasons.append(f"Balai Lelang di DB ('{row['balai_lelang']}') beda dengan PDF ('{balai_pdf}')")
                    
                    db_tgl_raw = str(row['tgl_pengajuan']).strip()
                    if pd.isna(row['tgl_pengajuan']) or db_tgl_raw in ['', 'nan', 'None']:
                        reasons.append("Tanggal pengajuan di DB masih KOSONG")
                    elif row['tgl_std'] != tgl_pdf_std:
                        reasons.append(f"Tgl Pengajuan di DB ('{db_tgl_raw}') beda dengan PDF ('{tgl_pdf_raw}')")
                
                unique_reasons = list(dict.fromkeys(reasons))
                nopol_details.append({
                    "Nopol": nopol,
                    "Status": "❌ Salah",
                    "Detail Alasan": " | ".join(unique_reasons)
                })

    summary_msg = "Semua Nopol di PDF terverifikasi cocok dengan Database!" if is_fully_valid else "Ada Nopol di PDF yang tidak sesuai / belum lengkap di Database."
    
    return is_fully_valid, summary_msg, pd.DataFrame(nopol_details)

def sign_pdf_bytes(doc, signature_bytes):
    target_page = None
    text_instances = []
    
    for page in doc:
        instances = page.search_for("Pemohon,")
        if instances:
            target_page = page
            text_instances = instances
            break
            
    if not target_page:
        target_page = doc[0]
        text_instances = target_page.search_for("Pemohon")
        
    if text_instances:
        rect = text_instances[0]
        sig_rect = fitz.Rect(
            rect.x0 - 10,
            rect.y0 + 15,
            rect.x0 + 80,
            rect.y0 + 55
        )
        target_page.insert_image(sig_rect, stream=signature_bytes)
    
    output_stream = io.BytesIO()
    doc.save(output_stream)
    doc.close()
    return output_stream.getvalue()

# ==========================================
# INTERFACE STREAMLIT UTAMA & MENU SIDEBAR
# ==========================================

# 1. MENU NAVIGASI
st.sidebar.header("🧭 Menu Utama")
menu_pilihan = st.sidebar.radio(
    "Pilih Fitur Aplikasi:",
    ["✍️ TTD & Verifikasi BPKB", "📄 Buat File Excel Pengajuan"]
)

st.sidebar.divider()

# 2. LOAD DATABASE (Muncul di semua menu)
st.sidebar.header("📁 Sumber Data Database")
db_option = st.sidebar.radio(
    "Pilih Sumber Database:",
    ["Google Sheets URL", "Upload File Excel / CSV"]
)

db_df = None

if db_option == "Google Sheets URL":
    gsheet_url = st.sidebar.text_input(
        "Paste URL Google Sheet:",
        value="https://docs.google.com/spreadsheets/d/1yvF9YstKN29QKWR_QHyaZml4PpH0A9xrcRndcCMBMrI/"
    )
    sheet_tab_name = st.sidebar.text_input("Nama Sheet/Tab (Opsional, cth: Sheet1):")
    if gsheet_url:
        try:
            raw_df = load_gsheet_data(gsheet_url, sheet_tab_name)
            db_df = standardize_db_columns(raw_df)
            st.sidebar.success(f"✅ Google Sheet Berhasil Dimuat ({len(db_df)} baris data)")
        except Exception as e:
            st.sidebar.error(f"❌ Gagal memuat Google Sheet: {e}")
else:
    uploaded_db = st.sidebar.file_uploader("Upload Database (Excel/CSV)", type=["xlsx", "xls", "csv"])
    if uploaded_db:
        try:
            if uploaded_db.name.endswith('.csv'):
                raw_df = pd.read_csv(uploaded_db)
            else:
                raw_df = pd.read_excel(uploaded_db)
            db_df = standardize_db_columns(raw_df)
            st.sidebar.success(f"✅ File Database Dimuat ({len(db_df)} baris data)")
        except Exception as e:
            st.sidebar.error(f"❌ Gagal membaca file: {e}")


# ==========================================
# FITUR 1: TTD & VERIFIKASI BPKB (Logic lama utuh 100%)
# ==========================================
if menu_pilihan == "✍️ TTD & Verifikasi BPKB":
    
    st.title("✍️ Otomatisasi Verifikasi & Tanda Tangan BPKB")
    st.markdown("Verifikasi data Nopol, Balai Lelang, dan Tanggal Pengajuan dari **Google Sheets** dengan File PDF sebelum TTD ditempelkan.")

    with st.expander("📌 **PANDUAN & CATATAN UNTUK USER BARU** (Klik untuk Membuka)", expanded=False):
        st.markdown("""
        ### 📋 Format Kolom & Tanggal Google Sheets:
        * **Urutan Kolom**: Kolom A = `Nopol`, Kolom B = `Balai Lelang`, Kolom C = `Tanggal Pengajuan`.
        * **Format Tanggal**: Sistem mendukung format **`DD/MM/YYYY`** (contoh: `18/09/2026`).
        
        ### ⚠️️ Syarat Penandatanganan:
        * File PDF akan ditandatangani **HANYA jika** seluruh Nopol yang tertera di PDF terdaftar di Google Sheet dengan Balai Lelang & Tanggal Pengajuan yang **100% cocok**.
        """)
        
    st.sidebar.divider()
    st.sidebar.header("📄 Upload Dokumen PDF")
    
    ttd_bytes = None
    if os.path.exists("TTD.png"):
        with open("TTD.png", "rb") as f:
            ttd_bytes = f.read()
        st.sidebar.success("✅ Gambar TTD otomatis terpasang dari sistem!")
    else:
        st.sidebar.warning("⚠️ File 'TTD.png' tidak ditemukan di dalam folder sistem!")
        uploaded_ttd = st.sidebar.file_uploader("Upload Gambar TTD JW (.png)", type=["png"])
        if uploaded_ttd is not None:
            ttd_bytes = uploaded_ttd.read()

    uploaded_pdfs = st.sidebar.file_uploader("Upload File PDF Form BPKB", type=["pdf"], accept_multiple_files=True)

    if st.sidebar.button("🚀 Jalankan Verifikasi & TTD", type="primary"):
        if db_df is None:
            st.error("❌ Database belum berhasil dimuat! Periksa URL Google Sheet atau Upload File Anda.")
        elif not ttd_bytes:
            st.error("❌ Mohon pastikan ada file Gambar TTD (.png) terlebih dahulu!")
        elif not uploaded_pdfs:
            st.error("❌ Mohon upload minimal 1 File PDF!")
        else:
            results = []
            detailed_reports = {}
            signed_files = {}
            
            st.subheader("📊 Laporan Hasil Verifikasi & TTD")
            progress_bar = st.progress(0)
            
            for idx, pdf_file in enumerate(uploaded_pdfs):
                pdf_bytes = pdf_file.read()
                pdf_data = extract_pdf_data(pdf_bytes, pdf_file.name)
                
                is_valid, msg, detail_df = validate_data_detailed(pdf_data, db_df)
                detailed_reports[pdf_file.name] = detail_df
                
                if is_valid:
                    signed_bytes = sign_pdf_bytes(pdf_data["doc"], ttd_bytes)
                    signed_files[f"SIGNED_{pdf_file.name}"] = signed_bytes
                    status_icon = "✅ BERHASIL DI-TTD"
                else:
                    status_icon = "❌ DITOLAK (ADA KETIDAKSESUAIAN)"
                
                results.append({
                    "Nama File PDF": pdf_file.name,
                    "Balai Lelang": pdf_data["balai_lelang"],
                    "Tgl Pengajuan PDF": pdf_data["tgl_raw"],
                    "Jumlah Nopol PDF": len(pdf_data["nopols"]),
                    "Status": status_icon,
                    "Ringkasan": msg
                })
                
                progress_bar.progress((idx + 1) / len(uploaded_pdfs))
                
            st.dataframe(pd.DataFrame(results), use_container_width=True)
            
            st.markdown("### 🔍 Rincian Diagnostik Nopol Per-File PDF")
            for pdf_name, df_report in detailed_reports.items():
                if not df_report.empty:
                    valid_count = len(df_report[df_report['Status'] == '✅ Benar'])
                    total_count = len(df_report)
                    
                    with st.expander(f"📄 Detail Analisis Nopol: **{pdf_name}** ({valid_count}/{total_count} Nopol Sesuai)"):
                        st.dataframe(df_report, use_container_width=True)
            
            if signed_files:
                st.success(f"🎉 **{len(signed_files)}** file PDF berhasil ditandatangani!")
                if len(signed_files) > 1:
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                        for fname, fbytes in signed_files.items():
                            zip_file.writestr(fname, fbytes)
                    
                    st.download_button(
                        label="📥 Download Semua PDF Ter-TTD (.ZIP)",
                        data=zip_buffer.getvalue(),
                        file_name="Hasil_TTD_Pengajuan_BPKB.zip",
                        mime="application/zip"
                    )
                else:
                    fname = list(signed_files.keys())[0]
                    st.download_button(
                        label=f"📥 Download {fname}",
                        data=signed_files[fname],
                        file_name=fname,
                        mime="application/pdf"
                    )

# ==========================================
# FITUR 2: BUAT FILE EXCEL PENGAJUAN
# ==========================================
elif menu_pilihan == "📄 Buat File Excel Pengajuan":
    
    st.title("📄 Pembuatan File Excel Pengajuan BPKB")
    st.markdown("Fitur ini memecah daftar Nopol berdasarkan **Balai Lelang** ke dalam sheet Excel yang berbeda pada *Tanggal Pengajuan* tertentu secara otomatis.")
    
    if db_df is None:
        st.warning("⚠️ Silakan muat database dari menu Sidebar terlebih dahulu.")
    else:
        # Terapkan fungsi parsing tanggal pada seluruh data agar format seragam
        db_df['tgl_std'] = db_df['tgl_pengajuan'].apply(parse_date_standard)
        
        # Ambil tanggal-tanggal unik yang tidak kosong dari kolom 'tgl_pengajuan'
        available_dates = [d for d in db_df['tgl_std'].unique() if pd.notna(d) and str(d).strip() != ""]
        available_dates.sort(reverse=True)
        
        if not available_dates:
            st.error("❌ Tidak ada data Tanggal Pengajuan di dalam Database.")
        else:
            col1, col2 = st.columns([1, 2])
            with col1:
                selected_date = st.selectbox("📅 Pilih Tanggal Pengajuan:", available_dates)
            
            # Preview Filter
            filtered_df = db_df[db_df['tgl_std'] == selected_date]
            st.info(f"Ditemukan **{len(filtered_df)}** kendaraan untuk pengajuan tanggal **{selected_date}**.")
            st.dataframe(filtered_df[['nopol', 'balai_lelang', 'tgl_pengajuan']].head(10), use_container_width=True)
            
            if st.button("⚙️ Generate Excel Pengajuan", type="primary"):
                output = io.BytesIO()
                
                # Gunakan openpyxl sebagai engine (standar bawaan pandas modern)
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    balai_list = filtered_df['balai_lelang'].dropna().unique()
                    
                    for balai in balai_list:
                        # 1. Bersihkan Nama Sheet (Hindari karakter ilegal & maksimal 31 karakter)
                        safe_sheet_name = re.sub(r'[\\/*?:\[\]]', '', str(balai))[:31]
                        if not safe_sheet_name.strip():
                            safe_sheet_name = "Sheet"
                            
                        df_balai = filtered_df[filtered_df['balai_lelang'] == balai].copy()
                        
                        # 2. Logic Kolom 'Terlampir'
                        # Deteksi kolom 'Tanggal Selling'
                        selling_col = [c for c in df_balai.columns if 'SELLING' in normalize_text(c)]
                        if selling_col:
                            sc = selling_col[0] # Ambil kolom pertama yg match dengan "Selling"
                            df_balai['Terlampir'] = df_balai[sc].apply(
                                lambda x: "Stock" if pd.isna(x) or str(x).strip() in ["", "nan", "None", "NaT"] else str(x).strip()
                            )
                        else:
                            df_balai['Terlampir'] = "Stock"
                            
                        # 3. Kumpulkan & Susun Kolom untuk Output Excel
                        export_data = {'No': range(1, len(df_balai) + 1)}
                        
                        # Ekstrak Nopol
                        export_data['Nopol'] = df_balai['nopol'] if 'nopol' in df_balai.columns else df_balai.iloc[:, 0]
                        
                        # Fungsi pencarian aman untuk kolom opsional (Type, Tahun, Engine)
                        def get_col_safely(keyword, df_source):
                            matched = [c for c in df_source.columns if keyword in normalize_text(c)]
                            return df_source[matched[0]] if matched else pd.Series(["-"] * len(df_source), index=df_source.index)
                            
                        export_data['Type'] = get_col_safely("TYPE", df_balai)
                        export_data['Tahun'] = get_col_safely("TAHUN", df_balai)
                        export_data['Engine'] = get_col_safely("ENGINE", df_balai)
                        export_data['Terlampir'] = df_balai['Terlampir']
                        
                        # Convert ke DF lalu save ke sheet
                        df_export = pd.DataFrame(export_data)
                        df_export.to_excel(writer, sheet_name=safe_sheet_name, index=False)
                        
                excel_data = output.getvalue()
                
                st.success("✅ File Excel Pengajuan BPKB berhasil dibuat!")
                st.download_button(
                    label=f"📥 Download File Pengajuan BPKB ({selected_date}).xlsx",
                    data=excel_data,
                    file_name=f"Pengajuan_BPKB_{selected_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )