import os
import shutil
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE

# Paths
SOURCE_DIR = r"C:\Users\Asus\.gemini\antigravity\brain\e6d98cf1-fec3-4d88-bb20-8c0811733046"
ASSETS_DIR = r"c:\Users\Asus\Documents\quantum\presentation_assets"
os.makedirs(ASSETS_DIR, exist_ok=True)

img_portrait_src = os.path.join(SOURCE_DIR, "character_portrait_1789617801229.jpg")
img_teacher_src = os.path.join(SOURCE_DIR, "teacher_classroom_1789701320604.jpg")
img_dancing_src = os.path.join(SOURCE_DIR, "dancing_fitness_food_1789701345077.jpg")
img_routine_src = os.path.join(SOURCE_DIR, "daily_routine_work_1789617817147.jpg")

img_portrait_dst = os.path.join(ASSETS_DIR, "character_portrait.jpg")
img_teacher_dst = os.path.join(ASSETS_DIR, "teacher_classroom.jpg")
img_dancing_dst = os.path.join(ASSETS_DIR, "dancing_fitness_food.jpg")
img_routine_dst = os.path.join(ASSETS_DIR, "daily_routine.jpg")

for src, dst in [
    (img_portrait_src, img_portrait_dst),
    (img_teacher_src, img_teacher_dst),
    (img_dancing_src, img_dancing_dst),
    (img_routine_src, img_routine_dst)
]:
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"Copied {os.path.basename(src)} -> {os.path.basename(dst)}")

# Presentation setup (16:9 widescreen)
prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

# Color Palette
COLOR_SENA_DARK = RGBColor(0, 50, 77)     # #00324D
COLOR_SENA_GREEN = RGBColor(57, 169, 0)   # #39A900
COLOR_TEXT_MAIN = RGBColor(15, 23, 42)    # #0F172A
COLOR_TEXT_BODY = RGBColor(51, 65, 85)     # #334155
COLOR_TEXT_MUTED = RGBColor(100, 116, 139)# #64748B
COLOR_WHITE = RGBColor(255, 255, 255)
COLOR_BG_CARD = RGBColor(248, 250, 252)   # #F8FAFC
COLOR_ACCENT_BLUE = RGBColor(14, 116, 144)# #0E7490

blank_layout = prs.slide_layouts[6]

def add_header(slide, title_text, category_text=""):
    header_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(1.1))
    tf = header_box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0
    
    if category_text:
        p_cat = tf.paragraphs[0]
        p_cat.text = category_text.upper()
        p_cat.font.size = Pt(11)
        p_cat.font.bold = True
        p_cat.font.color.rgb = COLOR_SENA_GREEN
        p_cat.font.name = "Arial"
        p_title = tf.add_paragraph()
    else:
        p_title = tf.paragraphs[0]
        
    p_title.text = title_text
    p_title.font.size = Pt(26)
    p_title.font.bold = True
    p_title.font.color.rgb = COLOR_SENA_DARK
    p_title.font.name = "Arial"

# ----------------- SLIDE 1: COVER -----------------
slide1 = prs.slides.add_slide(blank_layout)

bg1 = slide1.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
bg1.fill.solid()
bg1.fill.fore_color.rgb = COLOR_SENA_DARK
bg1.line.fill.background()

stripe = slide1.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(1.0), Inches(0.15), Inches(5.5))
stripe.fill.solid()
stripe.fill.fore_color.rgb = COLOR_SENA_GREEN
stripe.line.fill.background()

tb1 = slide1.shapes.add_textbox(Inches(1.2), Inches(0.9), Inches(7.2), Inches(5.7))
tf1 = tb1.text_frame
tf1.word_wrap = True

p = tf1.paragraphs[0]
p.text = "SERVICIO NACIONAL DE APRENDIZAJE — SENA"
p.font.size = Pt(12)
p.font.bold = True
p.font.color.rgb = COLOR_SENA_GREEN

p = tf1.add_paragraph()
p.text = "Evidence GA2-240202501-AA1-EV02 • Video Presentation"
p.font.size = Pt(14)
p.font.bold = True
p.font.color.rgb = RGBColor(148, 163, 184)
p.space_before = Pt(6)

p = tf1.add_paragraph()
p.text = "Presenting My Sister:\nNathalia Torres"
p.font.size = Pt(30)
p.font.bold = True
p.font.color.rgb = COLOR_WHITE
p.space_before = Pt(8)

p = tf1.add_paragraph()
p.text = "Oral English Presentation: Describing a Special Person"
p.font.size = Pt(15)
p.font.color.rgb = RGBColor(226, 232, 240)

# Details
details = [
    ("\n• APPRENTICE:", " Geralding Torres Gutierrez", COLOR_WHITE, True),
    ("• INSTRUCTOR:", " Silvana Tarud (Instructor Transversal Inglés)", RGBColor(203, 213, 225), False),
    ("• PROGRAM:", " Gestión Contable y de Información Financiera", RGBColor(203, 213, 225), False),
    ("• FICHA:", " 3466233 • Regional Atlántico (Centro de Comercio y Servicios)", COLOR_SENA_GREEN, True),
    ("• DATE:", " 2026", RGBColor(148, 163, 184), False),
]

for label, val, color, bold in details:
    p = tf1.add_paragraph()
    p.text = label + val
    p.font.size = Pt(12)
    p.font.color.rgb = color
    p.font.bold = bold

if os.path.exists(img_portrait_dst):
    slide1.shapes.add_picture(img_portrait_dst, Inches(8.7), Inches(1.3), Inches(4.0), Inches(4.0))

slide1.notes_slide.notes_text_frame.text = (
    "SPEECH SCRIPT (Slide 1 - Portada):\n"
    "\"Good morning, dear instructor Silvana Tarud and classmates. "
    "My name is Geralding Torres Gutierrez. I am an apprentice in Accounting and Financial Information Management, "
    "code 3466233, from Centro de Comercio y Servicios, Regional Atlántico. "
    "Today, I am going to present a very special person in my life: my sister, Nathalia Torres.\""
)

# ----------------- SLIDE 2: PERSONAL INFORMATION -----------------
slide2 = prs.slides.add_slide(blank_layout)
add_header(slide2, "Personal Profile: Who is She?", "Slide 02 • Personal Information")

if os.path.exists(img_portrait_dst):
    slide2.shapes.add_picture(img_portrait_dst, Inches(0.8), Inches(1.7), Inches(4.0), Inches(4.0))

card2 = slide2.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(5.2), Inches(1.7), Inches(7.3), Inches(5.2))
card2.fill.solid()
card2.fill.fore_color.rgb = COLOR_BG_CARD
card2.line.color.rgb = RGBColor(226, 232, 240)

tb2 = slide2.shapes.add_textbox(Inches(5.5), Inches(1.9), Inches(6.7), Inches(4.8))
tf2 = tb2.text_frame
tf2.word_wrap = True

lines_slide2 = [
    ("MEET MY SISTER", True, COLOR_SENA_DARK, 18),
    ("This is Nathalia Torres, a wonderful and inspiring woman.", False, COLOR_TEXT_BODY, 14),
    ("", False, COLOR_TEXT_MAIN, 6),
    ("KEY PERSONAL FACTS:", True, COLOR_SENA_GREEN, 14),
    ("• Full Name: Nathalia Torres", False, COLOR_TEXT_MAIN, 14),
    ("• Age: She is 29 years old", False, COLOR_TEXT_MAIN, 14),
    ("• Birthday: Her birthday is on May 13th", False, COLOR_TEXT_MAIN, 14),
    ("• Nationality: She is Colombian", False, COLOR_TEXT_MAIN, 14),
    ("• City of Residence: She lives in Barranquilla, Colombia", False, COLOR_TEXT_MAIN, 14),
    ("• Relationship: She is my beloved older sister", False, COLOR_TEXT_MAIN, 14),
    ("• Personality: She is very cheerful, kind, energetic, and dedicated.", False, COLOR_TEXT_MAIN, 14),
]

for text, bold, color, size in lines_slide2:
    p = tf2.add_paragraph() if tf2.paragraphs[0].text else tf2.paragraphs[0]
    p.text = text
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.size = Pt(size)

slide2.notes_slide.notes_text_frame.text = (
    "SPEECH SCRIPT (Slide 2 - Datos Personales):\n"
    "\"Let me introduce my sister, Nathalia Torres. "
    "She is 29 years old, and her birthday is on May 13th. "
    "She is Colombian, and she currently lives in the city of Barranquilla. "
    "She is my older sister, and she has a wonderful personality: she is always cheerful, kind, and full of positive energy.\""
)

# ----------------- SLIDE 3: OCCUPATION (TEACHER) -----------------
slide3 = prs.slides.add_slide(blank_layout)
add_header(slide3, "Profession: Elementary School Teacher", "Slide 03 • Occupation & Career")

if os.path.exists(img_teacher_dst):
    slide3.shapes.add_picture(img_teacher_dst, Inches(0.8), Inches(1.7), Inches(5.0), Inches(3.75))

card3 = slide3.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.2), Inches(1.7), Inches(6.3), Inches(5.2))
card3.fill.solid()
card3.fill.fore_color.rgb = COLOR_BG_CARD
card3.line.color.rgb = RGBColor(226, 232, 240)

tb3 = slide3.shapes.add_textbox(Inches(6.5), Inches(1.9), Inches(5.7), Inches(4.8))
tf3 = tb3.text_frame
tf3.word_wrap = True

lines_slide3 = [
    ("HER VOCATION: TEACHING", True, COLOR_SENA_DARK, 18),
    ("She educates and inspires young minds every single day.", False, COLOR_TEXT_BODY, 14),
    ("", False, COLOR_TEXT_MAIN, 6),
    ("PROFESSIONAL DETAILS:", True, COLOR_SENA_GREEN, 14),
    ("• Profession: Elementary School Teacher", False, COLOR_TEXT_MAIN, 14),
    ("• Workplace: A primary school in Barranquilla", False, COLOR_TEXT_MAIN, 14),
    ("• Work Schedule: Monday to Friday, from 7:00 AM to 2:30 PM", False, COLOR_TEXT_MAIN, 14),
    ("• Daily Responsibilities:", True, COLOR_SENA_DARK, 13),
    ("   - She prepares creative lesson plans and educational games.", False, COLOR_TEXT_BODY, 13),
    ("   - She teaches reading, science, and values to children.", False, COLOR_TEXT_BODY, 13),
    ("   - She grades homework and supports parents with dedication.", False, COLOR_TEXT_BODY, 13),
]

for text, bold, color, size in lines_slide3:
    p = tf3.add_paragraph() if tf3.paragraphs[0].text else tf3.paragraphs[0]
    p.text = text
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.size = Pt(size)

slide3.notes_slide.notes_text_frame.text = (
    "SPEECH SCRIPT (Slide 3 - Ocupación):\n"
    "\"Regarding her occupation, Nathalia works as a primary school teacher at an elementary school in Barranquilla. "
    "She works from Monday to Friday, from 7:00 AM to 2:30 PM. "
    "During her workday, she teaches children, prepares fun educational activities, and grades homework. "
    "She truly loves teaching and feels very proud of her students.\""
)

# ----------------- SLIDE 4: DAILY ROUTINE -----------------
slide4 = prs.slides.add_slide(blank_layout)
add_header(slide4, "Daily Routine & Everyday Habits", "Slide 04 • Everyday Life (Present Simple)")

routine_steps = [
    ("🌅 Morning Routine", [
        "• Wakes up early at 5:30 AM",
        "• Takes a fresh shower",
        "• Drinks a cup of hot coffee",
        "• Prepares her breakfast",
        "• Arrives at school at 6:45 AM"
    ], Inches(0.8), RGBColor(245, 158, 11)),
    ("🍎 Afternoon Routine", [
        "• Finishes classes at 2:30 PM",
        "• Has a nutritious lunch",
        "• Goes to the gym to work out",
        "• Takes a short rest",
        "• Plans tomorrow's classes"
    ], Inches(4.8), RGBColor(2, 132, 199)),
    ("🌙 Evening Routine", [
        "• Returns home around 6:30 PM",
        "• Cooks or enjoys dinner",
        "• Dances to Latin music",
        "• Talks with the family",
        "• Goes to sleep at 10:00 PM"
    ], Inches(8.8), RGBColor(139, 92, 246))
]

for title, bullets, x_pos, accent_color in routine_steps:
    card = slide4.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x_pos, Inches(1.8), Inches(3.7), Inches(5.0))
    card.fill.solid()
    card.fill.fore_color.rgb = COLOR_BG_CARD
    card.line.color.rgb = RGBColor(203, 213, 225)
    
    tb = slide4.shapes.add_textbox(x_pos + Inches(0.2), Inches(2.0), Inches(3.3), Inches(4.6))
    tf = tb.text_frame
    tf.word_wrap = True
    
    p = tf.paragraphs[0]
    p.text = title
    p.font.bold = True
    p.font.size = Pt(16)
    p.font.color.rgb = COLOR_SENA_DARK
    
    for b in bullets:
        p_b = tf.add_paragraph()
        p_b.text = b
        p_b.font.size = Pt(13)
        p_b.font.color.rgb = COLOR_TEXT_BODY
        p_b.space_before = Pt(8)

slide4.notes_slide.notes_text_frame.text = (
    "SPEECH SCRIPT (Slide 4 - Rutina Diaria):\n"
    "\"Now, let's talk about her daily routine. "
    "In the morning, she wakes up early at 5:30 AM, takes a shower, drinks fresh coffee, and arrives at school at 6:45 AM. "
    "In the afternoon, she finishes her classes at 2:30 PM, eats lunch, and goes to the gym to exercise. "
    "In the evening, she returns home, prepares a delicious dinner, dances for a while, and goes to bed at 10:00 PM.\""
)

# ----------------- SLIDE 5: HOBBIES & FREE TIME -----------------
slide5 = prs.slides.add_slide(blank_layout)
add_header(slide5, "Hobbies & Free Time: Dancing, Fitness & Food", "Slide 05 • Passions & Likes")

if os.path.exists(img_dancing_dst):
    slide5.shapes.add_picture(img_dancing_dst, Inches(0.8), Inches(1.7), Inches(5.0), Inches(3.75))

card5 = slide5.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.2), Inches(1.7), Inches(6.3), Inches(5.2))
card5.fill.solid()
card5.fill.fore_color.rgb = COLOR_BG_CARD
card5.line.color.rgb = RGBColor(226, 232, 240)

tb5 = slide5.shapes.add_textbox(Inches(6.5), Inches(1.9), Inches(5.7), Inches(4.8))
tf5 = tb5.text_frame
tf5.word_wrap = True

lines_slide5 = [
    ("WHAT SHE LOVES DOING", True, COLOR_SENA_DARK, 18),
    ("", False, COLOR_TEXT_MAIN, 4),
    ("HER FAVORITE HOBBIES:", True, COLOR_SENA_GREEN, 14),
    ("💃 Dancing (Bailar):", True, COLOR_SENA_DARK, 14),
    ("   She loves dancing salsa, merengue, and champeta.", False, COLOR_TEXT_BODY, 13),
    ("🏋️ Exercising (Ejercitarse):", True, COLOR_SENA_DARK, 14),
    ("   She works out at the gym 4 days a week to stay active and healthy.", False, COLOR_TEXT_BODY, 13),
    ("🥑 Food & Gastronomy (Comer):", True, COLOR_SENA_DARK, 14),
    ("   She enjoys tasting new recipes and eating delicious typical food.", False, COLOR_TEXT_BODY, 13),
    ("", False, COLOR_TEXT_MAIN, 4),
    ("CLOSING:", True, COLOR_SENA_DARK, 13),
    ("Thank you very much for your attention!", True, COLOR_SENA_GREEN, 17),
]

for text, bold, color, size in lines_slide5:
    p = tf5.add_paragraph() if tf5.paragraphs[0].text else tf5.paragraphs[0]
    p.text = text
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.size = Pt(size)

slide5.notes_slide.notes_text_frame.text = (
    "SPEECH SCRIPT (Slide 5 - Hobbies y Despedida):\n"
    "\"In her free time, Nathalia has three main passions: dancing, exercising, and enjoying delicious food! "
    "She loves dancing salsa and Caribbean music. She also exercises regularly at the gym to stay strong and healthy. "
    "Finally, she loves cooking and tasting new dishes with family and friends. "
    "That concludes my presentation. Thank you very much for your time and attention! Have a wonderful day.\""
)

# Save PPTX
pptx_path = r"c:\Users\Asus\Documents\quantum\diapositivas_presentacion_ingles.pptx"
prs.save(pptx_path)
print(f"PowerPoint successfully updated at: {pptx_path}")

desktop_pptx = r"C:\Users\Asus\Desktop\diapositivas_presentacion_ingles.pptx"
shutil.copy2(pptx_path, desktop_pptx)
print(f"PowerPoint copied to Desktop: {desktop_pptx}")
