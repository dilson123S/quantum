"""
Generador de Documento Técnico en Formato APA 7ma Edición (Times New Roman 12 pt)
sobre la Arquitectura de Inteligencia Artificial, Machine Learning y Sistemas de Decisión
del Ecosistema Quantum Trading Bot.
"""
from __future__ import annotations

import os
import shutil
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT


class APANumberedCanvas(canvas.Canvas):
    """Canvas de ReportLab que implementa el encabezado y foliación estricta APA 7ma edición."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()
        self.setFont("Times-Roman", 12)
        # Encabezado APA 7ma: Título corto a la izquierda y número de página alineado a la derecha en 540 pt
        self.drawString(72, 756, "ARQUITECTURA DE IA Y ML EN QUANTUM TRADING BOT")
        self.drawRightString(540, 756, str(self._pageNumber))
        self.restoreState()


def build_pdf(filename: str) -> None:
    # Dimensiones estándar Letter: 8.5 x 11 pulgadas (612 x 792 pt).
    # Márgenes exactos de 1 pulgada (72 pt) en los cuatro costados.
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=72,
        rightMargin=72,
        topMargin=72,
        bottomMargin=72,
    )

    # Definición de estilos tipográficos estricta APA 7ma (Times New Roman a 12 pt)
    styles = getSampleStyleSheet()

    # Párrafo ordinario APA: Times-Roman 12pt, leading 18pt, sangría de 0.5 in (36pt)
    apa_p = ParagraphStyle(
        "APAParagraph",
        fontName="Times-Roman",
        fontSize=12,
        leading=18,
        alignment=TA_JUSTIFY,
        firstLineIndent=36,
        spaceAfter=6,
    )

    # Párrafo de bloque sin sangría (utilizado en el resumen / abstract)
    apa_p_noindent = ParagraphStyle(
        "APAParagraphNoIndent",
        fontName="Times-Roman",
        fontSize=12,
        leading=18,
        alignment=TA_JUSTIFY,
        firstLineIndent=0,
        spaceAfter=6,
    )

    # Estilos de Portada
    title_cover_style = ParagraphStyle(
        "APATitleCover",
        fontName="Times-Bold",
        fontSize=12,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=18,
    )

    author_cover_style = ParagraphStyle(
        "APAAuthorCover",
        fontName="Times-Roman",
        fontSize=12,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=6,
    )

    author_note_title = ParagraphStyle(
        "APAAuthorNoteTitle",
        fontName="Times-Bold",
        fontSize=12,
        leading=18,
        alignment=TA_CENTER,
        spaceBefore=36,
        spaceAfter=8,
    )

    author_note_text = ParagraphStyle(
        "APAAuthorNoteText",
        fontName="Times-Roman",
        fontSize=11,
        leading=16,
        alignment=TA_JUSTIFY,
        firstLineIndent=36,
        spaceAfter=6,
    )

    # Estilos de Resumen (Página 2)
    abstract_heading = ParagraphStyle(
        "APAAbstractHeading",
        fontName="Times-Bold",
        fontSize=12,
        leading=18,
        alignment=TA_CENTER,
        spaceBefore=12,
        spaceAfter=14,
    )

    keywords_style = ParagraphStyle(
        "APAKeywords",
        fontName="Times-Roman",
        fontSize=12,
        leading=18,
        alignment=TA_JUSTIFY,
        firstLineIndent=36,
        spaceBefore=10,
        spaceAfter=12,
    )

    # Niveles de Encabezado APA 7ma
    heading_1 = ParagraphStyle(
        "APAHeading1",
        fontName="Times-Bold",
        fontSize=12,
        leading=18,
        alignment=TA_CENTER,
        spaceBefore=16,
        spaceAfter=8,
        keepWithNext=True,
    )

    heading_2 = ParagraphStyle(
        "APAHeading2",
        fontName="Times-Bold",
        fontSize=12,
        leading=18,
        alignment=TA_LEFT,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    heading_3 = ParagraphStyle(
        "APAHeading3",
        fontName="Times-BoldItalic",
        fontSize=12,
        leading=18,
        alignment=TA_LEFT,
        spaceBefore=12,
        spaceAfter=4,
        keepWithNext=True,
    )

    # Estilos de Tablas Académicas APA
    table_num_style = ParagraphStyle(
        "APATableNum",
        fontName="Times-Bold",
        fontSize=12,
        leading=16,
        alignment=TA_LEFT,
        spaceBefore=10,
        spaceAfter=2,
        keepWithNext=True,
    )

    table_title_style = ParagraphStyle(
        "APATableTitle",
        fontName="Times-Italic",
        fontSize=12,
        leading=16,
        alignment=TA_LEFT,
        spaceAfter=6,
        keepWithNext=True,
    )

    table_cell_style = ParagraphStyle(
        "APATableCell",
        fontName="Times-Roman",
        fontSize=10,
        leading=13,
        alignment=TA_LEFT,
    )

    table_cell_bold = ParagraphStyle(
        "APATableCellBold",
        fontName="Times-Bold",
        fontSize=10,
        leading=13,
        alignment=TA_LEFT,
    )

    table_note_style = ParagraphStyle(
        "APATableNote",
        fontName="Times-Italic",
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceBefore=4,
        spaceAfter=10,
    )

    # Estilo de Referencias con Sangría Francesa (Hanging Indent)
    ref_style = ParagraphStyle(
        "APAReference",
        fontName="Times-Roman",
        fontSize=12,
        leading=18,
        alignment=TA_LEFT,
        leftIndent=36,
        firstLineIndent=-36,
        spaceAfter=8,
    )

    story = []

    # =========================================================================
    # PÁGINA 1: PORTADA PROFESIONAL APA 7MA EDICIÓN
    # =========================================================================
    story.append(Spacer(1, 48))
    story.append(
        Paragraph(
            "Arquitectura Integral de Inteligencia Artificial, Aprendizaje Automático<br/>"
            "y Sistemas Cognitivos de Decisión en Quantum Trading Bot",
            title_cover_style,
        )
    )
    story.append(Spacer(1, 24))
    story.append(Paragraph("Equipo de Ingeniería Cuantitativa &amp; Inteligencia Artificial", author_cover_style))
    story.append(Paragraph("Quantum Algorithmic Technologies &amp; Laboratorio de Finanzas Cuantitativas", author_cover_style))
    story.append(Paragraph("Monografía Técnica de Arquitectura de Sistemas Autónomos de Inversión", author_cover_style))
    story.append(Paragraph("28 de octubre de 2026", author_cover_style))

    story.append(Spacer(1, 60))
    story.append(Paragraph("Nota del Autor", author_note_title))
    story.append(
        Paragraph(
            "Este documento técnico sintetiza el diseño, fundamentos matemáticos y despliegue operativo "
            "del ecosistema de negociación algorítmica Quantum Trading Bot. La investigación y desarrollo "
            "fueron conducidos de manera independiente en el entorno de pruebas y producción de alta "
            "frecuencia multiactivo. Toda correspondencia relativa a este manuscrito debe ser dirigida "
            "al repositorio central de ingeniería del proyecto.",
            author_note_text,
        )
    )

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 2: RESUMEN (ABSTRACT) Y PALABRAS CLAVE
    # =========================================================================
    story.append(Paragraph("Resumen", abstract_heading))
    story.append(
        Paragraph(
            "El presente documento técnico expone de manera exhaustiva la arquitectura de inteligencia "
            "artificial, aprendizaje automático y gobernanza de decisiones implementada en el ecosistema "
            "de trading algorítmico Quantum Trading Bot. Frente a las limitaciones de los enfoques "
            "tradicionales que dependen de modelos de caja negra no interpretables o de heurísticas técnicas "
            "rígidas, Quantum articula un marco computacional inspirado en la Teoría del Proceso Dual "
            "de Daniel Kahneman. Esta estructura sinérgica integra un Sistema 1 no autorregresivo de ultra-baja "
            "latencia (10 a 40 ms) compuesto por redes neuronales y codificadores ONNX/ModernBERT (Laya, Kev, "
            "NanoJev) para la evaluación heurística instantánea de coherencia de tesis, riesgo de trampas "
            "institucionales (<i>fakeouts</i>) y puntuación de evidencia técnica; y un Sistema 2 deliberativo y "
            "determinista basado en un motor de puntuación multicriterio de 5 dimensiones cuantitativas, "
            "conceptos de dinero inteligente institucional (<i>Smart Money Concepts</i>) y fractalidad horaria. "
            "Asimismo, se documenta el subsistema de aprendizaje profundo temporal compuesto por el modelo "
            "<i>TemporalGRUPredictor</i> (red recurrente bidireccional con autoatención y cabezales multitarea "
            "para dirección, probabilidad calibrada y volatilidad hacia adelante), el oráculo fundacional "
            "<i>Kronos</i> para proyección estocástica de trayectorias de velas japonesas (<i>K-lines</i>) como "
            "lenguaje formal, clasificadores tabulares de Gradient Boosting con 15 variables canónicas "
            "normalizadas, la compuerta elástica de riesgo (<i>Elastic Gating</i>) y el Laboratorio de "
            "Aprendizaje Autónomo (<i>Autonomous Learning Lab</i>) fundamentado en autopsias cognitivas "
            "post-trade y consolidación empírica continua.",
            apa_p_noindent,
        )
    )
    story.append(
        Paragraph(
            "<i>Palabras clave:</i> inteligencia artificial financiera, aprendizaje automático, teoría del "
            "proceso dual, redes neuronales recurrentes, transformadores de series temporales, Smart Money "
            "Concepts, compuertas elásticas de riesgo, microestructura de mercado, detección de trampas de liquidez.",
            keywords_style,
        )
    )

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 3 EN ADELANTE: CUERPO PRINCIPAL DEL TRABAJO
    # =========================================================================
    story.append(
        Paragraph(
            "Arquitectura Integral de Inteligencia Artificial, Aprendizaje Automático<br/>"
            "y Sistemas Cognitivos de Decisión en Quantum Trading Bot",
            heading_1,
        )
    )

    story.append(
        Paragraph(
            "El diseño de sistemas cuantitativos de negociación en mercados de capitales y derivados financieros "
            "enfrenta de forma sistemática el desafío de la no estacionariedad, la microestructura altamente ruidosa "
            "y la manipulación asimétrica de liquidez por parte de intermediarios institucionales. Históricamente, "
            "la industria se ha fragmentado entre dos filosofías metodológicas contrapuestas: por un lado, "
            "los algoritmos basados en reglas técnicas fijas (cruces de medias móviles, bandas de volatilidad "
            "estáticas), cuya rigidez provoca una rápida obsolescencia ante cambios de régimen de volatilidad; y "
            "por otro lado, modelos predictivos de aprendizaje supervisado monolíticos que actúan como cajas "
            "negras opacas, vulnerables al sobreajuste (<i>overfitting</i>) y desprovistas de mecanismos "
            "de control de riesgo interpretables.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "El ecosistema <b>Quantum Trading Bot</b> resuelve este dilema mediante una arquitectura híbrida de "
            "alta ingeniería que gestiona concurrentemente diversas clases de activos: metales preciosos "
            "(XAUUSD / Oro), divisas mayores (USDJPY), activos digitales (BTCUSD) e índices sintéticos con "
            "dinámica de saltos discretos o volatilidad estocástica pura (Crash 500, Boom 500, Volatility 75 "
            "y Volatility 100). En lugar de someter las decisiones a un único estimador aislado, la plataforma "
            "orquesta una jerarquía de agentes especializados que operan bajo protocolos matemáticos rigurosos, "
            "combinando la velocidad intuitiva de los codificadores neuronales de ultra-baja latencia con la "
            "disciplina lógica de motores deterministas de análisis estructural.",
            apa_p,
        )
    )

    # =========================================================================
    # SECCIÓN 2: EL PARADIGMA COGNITIVO DE LA TEORÍA DEL PROCESO DUAL
    # =========================================================================
    story.append(Paragraph("El Paradigma Cognitivo de la Teoría del Proceso Dual", heading_1))

    story.append(
        Paragraph(
            "El pilar epistemológico de Quantum Trading Bot se sustenta en la Teoría del Proceso Dual "
            "desarrollada en las ciencias cognitivas por Daniel Kahneman (2011). Este modelo postula que "
            "el juicio y la toma de decisiones emergen de la interacción entre dos subsistemas diferenciados: "
            "el <i>Sistema 1</i>, caracterizado por su funcionamiento veloz, automático, asociativo, subsimbólico "
            "y de mínimo costo computacional; y el <i>Sistema 2</i>, de naturaleza analítica, deliberativa, "
            "lógico-deductiva y normativamente estricta. En la arquitectura de Quantum, esta división no constituye "
            "una simple analogía conceptual, sino una especificación de diseño de software desacoplado que "
            "permite armonizar la inferencia probabilística con la preservación estricta del capital.",
            apa_p,
        )
    )

    story.append(Paragraph("Modelos de Inferencia Rápida de Sistema 1: Laya, Kev y NanoJev", heading_2))

    story.append(
        Paragraph(
            "El Sistema 1 de Quantum está integrado por una familia de modelos de código abierto y ejecución "
            "local optimizados mediante PyTorch y ONNX Runtime sobre hardware acelerado (GPU NVIDIA CUDA o CPU "
            "multihilo). Estos modelos son deliberadamente no autorregresivos: no generan texto libre ni sufren "
            "las alucinaciones sintácticas características de los modelos de lenguaje masivos generativos. Por el "
            "contrario, resuelven problemas de clasificación y regresión multiobjetivo en latencias que fluctúan "
            "entre 10 y 40 milisegundos por evento de barra cerrada:",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>1. Laya (Convai Innovations):</b> Basado en un codificador <i>ModernBERT</i> de 421 millones de "
            "parámetros exportado a ONNX. Laya opera como un clasificador binario de alta velocidad cuya función es "
            "evaluar la coherencia de cada candidato a operación. Sus inferencias producen tres diagnósticos: (a) "
            "<i>Alineación de tesis</i> (<code>aligned</code> vs. <code>contradictory</code>), comprobando si la "
            "dinámica de la acción del precio respalda la dirección solicitada; (b) <i>Puntuación de evidencia</i> "
            "(escala discreta de 1 a 5); y (c) <i>Evaluación de trampa</i> (<code>genuine_expansion</code> frente "
            "a <code>potential_trap</code>), detectando falsos quiebres institucionalmente inducidos.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>2. Kev (Jared Palmer):</b> Modelo contextual fundamentado en variantes compactas de Qwen (0.6B a "
            "9B parámetros). Kev está especializado en la contextualización macroeconómica y narrativa intersesión: "
            "examina la proximidad de noticias fundamentales de alto impacto en ForexFactory, evalúa las transiciones "
            "entre las sesiones de Asia, Londres y Nueva York, e incorpora el sesgo de política monetaria.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>3. NanoJev (PyTorch Residual Engine):</b> Red residual profunda calibrada de 0.6B parámetros "
            "optimizada para la inferencia paralela por lotes (<i>batching</i>). A diferencia de los evaluadores "
            "secuenciales que bloquean el bucle de trading, NanoJev analiza de forma simultánea en un único tensor "
            "matricial toda la canasta de activos monitoreados (Crash 500, Boom 500, Volatility 75, Volatility 100, "
            "XAUUSD y USDJPY), asegurando una latencia determinista sin interferir en los hilos de ejecución de MetaTrader 5.",
            apa_p,
        )
    )

    story.append(Paragraph("Deliberación Lógica y Verificación Determinista de Sistema 2", heading_2))

    story.append(
        Paragraph(
            "Frente a la velocidad asociativa del Sistema 1, el <i>Sistema 2</i> actúa como el guardián normativo "
            "y determinista. Este módulo no admite aproximaciones estadísticas difusas: exige el cumplimiento riguroso "
            "de reglas matemáticas de confluencia y estructura de mercado antes de autorizar cualquier transacción:",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>1. Motor de Puntuación Multicriterio (<code>ScoreEngine</code>):</b> Evalúa algorítmicamente cinco "
            "dimensiones independientes con una ponderación máxima total de 100 puntos: Estructura de Mercado "
            "(30 puntos; valida swings, quiebres de rango y fractalidad); Tendencia Multitemporal (25 puntos; exige "
            "alineación en M5, M15 y H1 con EMAs 20, 50 y 200, junto a fuerza direccional ADX); Momento (15 puntos; "
            "supervisa zonas dinámicas de RSI y pendientes de histograma MACD); Volumen (15 puntos; confirma la "
            "absorción y la tendencia del On-Balance Volume); y Volatilidad (15 puntos; mide la expansión de ratios ATR). "
            "Solo aquellos candidatos que alcanzan o superan el umbral configurado (65 u 80 puntos) son considerados aptos.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>2. Smart Money Concepts (SMC) &amp; Metodología Alchemist:</b> El Sistema 2 formaliza mecánicamente "
            "la liquidez institucional mediante la identificación de bloques de órdenes (<i>Order Blocks</i>), vacíos "
            "de liquidez no mitigados (<i>Fair Value Gaps</i> - FVG), quiebres de estructura (BOS) y cambios de carácter "
            "(CHoCH). Mediante la estrategia <i>Alchemist</i>, implementa los Soportes y Resistencias Malasios (MSNR) en "
            "gráficos de línea de precios de apertura y cierre (eliminando el ruido aleatorio de las mechas), la teoría "
            "CRT (<i>Candle Range Theory</i>) orientada a la vela de las 9:00 AM hora de Nueva York, y formaciones "
            "estructurales Quasimodo (QM).",
            apa_p,
        )
    )

    # TABLA 1: Comparación Sistema 1 vs Sistema 2
    story.append(Spacer(1, 8))
    story.append(Paragraph("Tabla 1", table_num_style))
    story.append(Paragraph("<i>Comparativa Arquitectónica entre el Sistema 1 y el Sistema 2 en Quantum Trading Bot</i>", table_title_style))

    table_data_1 = [
        [
            Paragraph("<b>Dimensión</b>", table_cell_bold),
            Paragraph("<b>Sistema 1 (Fast / Inferencia Neuronal)</b>", table_cell_bold),
            Paragraph("<b>Sistema 2 (Slow / Deliberación Algorítmica)</b>", table_cell_bold),
        ],
        [
            Paragraph("Paradigma Cognitivo", table_cell_style),
            Paragraph("Heurístico, subsimbólico, intuitivo y conexionista.", table_cell_style),
            Paragraph("Normativo, determinista, deductivo y simbólico.", table_cell_style),
        ],
        [
            Paragraph("Tecnología Central", table_cell_style),
            Paragraph("ModernBERT (ONNX), Qwen, Residual Net PyTorch.", table_cell_style),
            Paragraph("ScoreEngine (0-100 pts), SMC, Alchemist MSNR, CRT.", table_cell_style),
        ],
        [
            Paragraph("Latencia de Cómputo", table_cell_style),
            Paragraph("10 ms a 40 ms (ultra-baja latencia local en GPU/CPU).", table_cell_style),
            Paragraph("0.5 ms a 2 ms (cálculo matricial y vectorial NumPy).", table_cell_style),
        ],
        [
            Paragraph("Preguntas Clave", table_cell_style),
            Paragraph("¿La tesis es coherente? ¿Hay riesgo de trampa / fakeout?", table_cell_style),
            Paragraph("¿El score es &ge; 70? ¿Hay confirmación estructural?", table_cell_style),
        ],
        [
            Paragraph("Poder de Decisión", table_cell_style),
            Paragraph("Elastic Gating: Modula el Tier de riesgo o ejerce Veto.", table_cell_style),
            Paragraph("Verificación estricta: Bloquea si faltan confluencias.", table_cell_style),
        ],
    ]

    t1 = Table(table_data_1, colWidths=[110, 185, 175])
    t1.setStyle(
        TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
            ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.append(t1)
    story.append(
        Paragraph(
            "<i>Nota.</i> Elaboración propia basada en la microarquitectura de Quantum Trading Bot (2026).",
            table_note_style,
        )
    )

    # =========================================================================
    # SECCIÓN 3: MACHINE LEARNING Y DEEP LEARNING
    # =========================================================================
    story.append(Paragraph("Arquitecturas de Aprendizaje Automático y Redes Neuronales Profundas", heading_1))

    story.append(
        Paragraph(
            "El componente de aprendizaje computacional de Quantum Trading Bot comprende una jerarquía especializada "
            "de modelos diseñados para modelar dependencias temporales, distribuciones condicionales de éxito y "
            "volatilidad proyectada. Se evita la dependencia de un estimador único mediante una división funcional estricta.",
            apa_p,
        )
    )

    story.append(Paragraph("TemporalGRUPredictor: Red Neuronal Recurrente con Self-Attention Pooling", heading_2))

    story.append(
        Paragraph(
            "Para capturar la dinámica temporal secuencial sin incurrir en la latencia prohibitiva de transformadores "
            "pesados durante el análisis en tiempo real en velas de 5 minutos (M5), el módulo <code>ml.torch_models</code> "
            "despliega el modelo <code>TemporalGRUPredictor</code>. Este sistema articula una red neuronal recurrente "
            "basada en unidades cerradas bidireccionales (<i>Bi-directional Gated Recurrent Units</i>, Bi-GRU) combinadas "
            "con una capa de agregación por autoatención (<i>Self-Attention Pooling</i>).",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "Matemáticamente, dada una serie de características temporales <i>X</i> = (<i>x</i><sub>1</sub>, <i>x</i><sub>2</sub>, "
            "..., <i>x</i><sub><i>T</i></sub>), el modelo normaliza la entrada mediante <code>LayerNorm</code> y una proyección "
            "lineal con activación GELU. La recurrencia bidireccional computa los estados ocultos hacia adelante y hacia atrás, "
            "concatenándolos en un vector representativo <i>h</i><sub><i>t</i></sub> = [<i>h&#773;</i><sub><i>t</i></sub> || "
            "<i>h&#772;</i><sub><i>t</i></sub>]. La capa de autoatención determina el peso de cada vela calculando un escalar de "
            "relevancia <i>e</i><sub><i>t</i></sub> = <i>q</i><sup><i>T</i></sup> <i>h</i><sub><i>t</i></sub> mediante un vector de consulta "
            "entrenable <i>q</i>. Los coeficientes de atención normalizados &alpha;<sub><i>t</i></sub> = softmax(<i>e</i><sub><i>t</i></sub>) "
            "permiten sintetizar la secuencia en un vector latente de contexto <i>c</i> = &sum; &alpha;<sub><i>t</i></sub> <i>h</i><sub><i>t</i></sub>, "
            "el cual otorga mayor peso a eventos de clímax de volumen o absorción institucional.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "El vector contextual <i>c</i> transita a través de un tronco residual (<code>ResidualDenseBlock</code>) "
            "con conexiones de salto identidad <i>y</i> = <i>x</i> + Net(<i>x</i>) y regularización por <code>Dropout</code>, "
            "bifurcándose hacia tres cabezales de predicción simultáneos: (a) <b>Cabezal de Dirección:</b> clasificador "
            "multiclase que computa probabilidades Softmax para VENDER (-1), MANTENER (0) y COMPRAR (+1); (b) <b>Cabezal de Éxito:</b> "
            "estimador de probabilidad calibrada <i>P</i>(Éxito | Entrada) = &sigma;(<i>W</i><sub><i>s</i></sub> <i>c</i> + <i>b</i><sub><i>s</i></sub>) "
            "mediante función sigmoide; y (c) <b>Cabezal de Volatilidad:</b> proyector de volatilidad esperada forward que asegura "
            "valores reales estrictamente positivos mediante la función de activación Softplus ln(1 + <i>e</i><sup><i>z</i></sup>).",
            apa_p,
        )
    )

    story.append(Paragraph("NanoJevPyTorchNet: Red Residual Multiactivo de Inferencia de Estado", heading_2))

    story.append(
        Paragraph(
            "Para proporcionar un motor neural de inferencia de Sistema 1 independiente de llamadas remotas, "
            "<code>NanoJevPyTorchNet</code> procesa un vector de 16 evidencias estructuradas (distancias porcentuales a zonas "
            "de oferta/demanda, ratios de mechas de rechazo, régimen de medias móviles mayores y alineación RSI). Su topología "
            "residual compacta garantiza inferencias matriciales concurrentes en menos de 5 milisegundos, evaluando la probabilidad "
            "de expansiones genuinas frente a trampas de absorción de liquidez.",
            apa_p,
        )
    )

    story.append(Paragraph("Kronos Foundation Model Oracle: Modelado de Velas como Lenguaje Formal", heading_2))

    story.append(
        Paragraph(
            "El subsistema <code>ml.kronos_oracle</code> introduce la tecnología de modelos fundacionales de series de tiempo. "
            "Inspirado en los transformadores autorregresivos (Vaswani et al., 2017), el oráculo <b>Kronos</b> trata las secuencias "
            "de velas japonesas (<i>K-lines</i>) como un lenguaje formal financiero donde cada vela representa un token semántico. "
            "A través de mecanismos de autoatención profunda, Kronos genera simulaciones estocásticas de la trayectoria futura del "
            "precio a un horizonte de 5 a 20 periodos hacia adelante.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "A partir de dicha trayectoria proyectada, el oráculo emite un veredicto estructurado <code>KronosVerdict</code> "
            "que cuantifica la Máxima Excursión Favorable esperada (<i>Maximum Favorable Excursion</i>, MFE), la Máxima Excursión "
            "Adversa (<i>Maximum Adverse Excursion</i>, MAE) y la probabilidad relativa de alcanzar el Take-Profit antes de que el "
            "precio vulnere el Stop-Loss. Si el oráculo proyecta una probabilidad de toque de Stop-Loss superior a los umbrales de "
            "tolerancia, el candidato es vetado de forma profiláctica.",
            apa_p,
        )
    )

    story.append(Paragraph("Modelos Tabulares de Gradient Boosting y Feature Engineering Canónico", heading_2))

    story.append(
        Paragraph(
            "Como complemento de alta interpretabilidad, Quantum incorpora árboles de decisión potenciados por gradiente "
            "(XGBoost y LightGBM) en <code>ml.predictor</code>. La precisión de este clasificador descansa en un vector canónico "
            "de 15 características normalizadas (ver Tabla 2).",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "Una innovación matemática sustancial en este vector es el <b>multiplicador de orientación direccional</b> "
            "(<i>dir_mult</i>), definido como +1.0 para señales de compra y -1.0 para señales de venta. Al multiplicar "
            "las pendientes de medias móviles, divergencias de RSI y diferenciales de MACD por este factor, el espacio de "
            "características se torna matemáticamente simétrico e invariante respecto a la dirección del trade. De esta "
            "forma, valores positivos en el vector siempre reflejan fortaleza a favor de la operación, impidiendo que el "
            "árbol requiera particiones duplicadas para aprender patrones idénticos en sentido opuesto. Asimismo, la estacionalidad "
            "intradía se codifica mediante transformaciones armónicas cíclicas de Fourier (seno y coseno de la hora) para "
            "evitar saltos discontinuos entre las 23:59 y las 00:00 UTC.",
            apa_p,
        )
    )

    # TABLA 2: Vector Canónico de Características
    story.append(Spacer(1, 8))
    story.append(Paragraph("Tabla 2", table_num_style))
    story.append(Paragraph("<i>Vector Canónico de 15 Características Normalizadas para Inferencia de Machine Learning</i>", table_title_style))

    table_data_2 = [
        [
            Paragraph("<b>Variable</b>", table_cell_bold),
            Paragraph("<b>Definición Matemática / Lógica</b>", table_cell_bold),
            Paragraph("<b>Rango / Normalización</b>", table_cell_bold),
        ],
        [
            Paragraph("<code>ema20_slope</code>", table_cell_style),
            Paragraph("Pendiente porcentual de la EMA-20 entre barras consecutivas orientada por dirección.", table_cell_style),
            Paragraph("Real acotado [-0.05, 0.05].", table_cell_style),
        ],
        [
            Paragraph("<code>ema50_slope</code>", table_cell_style),
            Paragraph("Pendiente porcentual de la EMA-50 orientada por la dirección del trade.", table_cell_style),
            Paragraph("Real acotado [-0.05, 0.05].", table_cell_style),
        ],
        [
            Paragraph("<code>price_vs_ema200</code>", table_cell_style),
            Paragraph("Desviación porcentual del precio respecto a la EMA-200 macro.", table_cell_style),
            Paragraph("Porcentaje acotado [-5.0%, 5.0%].", table_cell_style),
        ],
        [
            Paragraph("<code>adx_norm</code>", table_cell_style),
            Paragraph("Intensidad de la fuerza tendencial medida por el oscilador ADX.", table_cell_style),
            Paragraph("Normalizado [0.0, 1.0] (ADX / 60).", table_cell_style),
        ],
        [
            Paragraph("<code>rsi_norm</code>", table_cell_style),
            Paragraph("Oscilador RSI centrado en 50 y orientado por la dirección del trade.", table_cell_style),
            Paragraph("Normalizado [-1.0, 1.0].", table_cell_style),
        ],
        [
            Paragraph("<code>macd_hist_slope</code>", table_cell_style),
            Paragraph("Diferencial de aceleración del histograma MACD respecto a la barra previa.", table_cell_style),
            Paragraph("Escalado por 100, acotado [-1.0, 1.0].", table_cell_style),
        ],
        [
            Paragraph("<code>atr_ratio_val</code>", table_cell_style),
            Paragraph("Ratio entre la volatilidad instantánea y la media histórica de ATR.", table_cell_style),
            Paragraph("Continuo [0.0, 4.0].", table_cell_style),
        ],
        [
            Paragraph("<code>volume_ratio_val</code>", table_cell_style),
            Paragraph("Ratio del volumen de ticks actual respecto a su media móvil de 20 periodos.", table_cell_style),
            Paragraph("Continuo [0.0, 5.0].", table_cell_style),
        ],
        [
            Paragraph("<code>obv_trend</code>", table_cell_style),
            Paragraph("Tendencia direccional acumulativa del On-Balance Volume (OBV).", table_cell_style),
            Paragraph("Discreto normalizado {-1, 0, 1}.", table_cell_style),
        ],
        [
            Paragraph("<code>spread_norm</code>", table_cell_style),
            Paragraph("Costo transaccional de spread evaluado como fracción del ATR actual.", table_cell_style),
            Paragraph("Ratio de fricción [0.0, 1.0].", table_cell_style),
        ],
        [
            Paragraph("<code>hour_sin</code> / <code>hour_cos</code>", table_cell_style),
            Paragraph("Codificación armónica cíclica de la hora del día en el ciclo de 24 horas.", table_cell_style),
            Paragraph("Proyección en círculo unitario [-1.0, 1.0].", table_cell_style),
        ],
        [
            Paragraph("<code>day_of_week</code>", table_cell_style),
            Paragraph("Componente de estacionalidad semanal de la sesión de negociación.", table_cell_style),
            Paragraph("Entero ordinal [0 = Lun, ..., 4 = Vie].", table_cell_style),
        ],
        [
            Paragraph("<code>structure_score_norm</code>", table_cell_style),
            Paragraph("Puntuación del módulo determinista de estructura de mercado (Sistema 2).", table_cell_style),
            Paragraph("Normalizado [0.0, 1.0] (Score / 30).", table_cell_style),
        ],
        [
            Paragraph("<code>trend_score_norm</code>", table_cell_style),
            Paragraph("Puntuación del módulo determinista de tendencia multitemporal (Sistema 2).", table_cell_style),
            Paragraph("Normalizado [0.0, 1.0] (Score / 25).", table_cell_style),
        ],
    ]

    t2 = Table(table_data_2, colWidths=[120, 230, 120])
    t2.setStyle(
        TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
            ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.append(t2)
    story.append(
        Paragraph(
            "<i>Nota.</i> Extraído de la especificación técnica en <code>ml.features</code> de Quantum Trading Bot.",
            table_note_style,
        )
    )

    # =========================================================================
    # SECCIÓN 4: SISTEMAS DE TOMA DE DECISIONES Y GOBERNANZA DE RIESGO
    # =========================================================================
    story.append(Paragraph("Sistemas de Toma de Decisiones y Gobernanza de Riesgo", heading_1))

    story.append(
        Paragraph(
            "Uno de los errores más graves en los sistemas de trading cuantitativo es la desconexión funcional entre "
            "las probabilidades emitidas por los modelos de machine learning y los algoritmos de asignación de capital. "
            "Quantum Trading Bot resuelve esta falla estructural mediante un protocolo de <b>Compuertas Elásticas de "
            "Riesgo</b> (<i>Elastic Gating</i>) y una gestión dinámica y asimétrica de cada posición.",
            apa_p,
        )
    )

    story.append(Paragraph("Compuertas Elásticas de Decisión y Niveles de Ejecución (Execution Tiers)", heading_2))

    story.append(
        Paragraph(
            "En lugar de un filtro binario rígido, cada oportunidad confirmada se clasifica en uno de tres "
            "<i>Niveles de Ejecución</i> (<code>ExecutionTier</code>) modulados por la convergencia entre el Sistema 1 "
            "y el Sistema 2 (ver Tabla 3):",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>1. TIER A ALPHA (Convicción Institucional Máxima):</b> Asignado cuando concurren un régimen mayor H1 "
            "alineado, un Score determinista superior a 80 puntos y la validación favorable de Sistema 1 con veredicto "
            "<code>aligned</code>, puntuación de evidencia &ge; 4 y riesgo de trampa catalogado como <code>genuine_expansion</code>. "
            "Se autoriza el 100% de la unidad de riesgo base (1.0x), se busca un ratio beneficio/riesgo ambicioso de 2.2R a "
            "2.5R y se activan piramidaciones libres de riesgo en fases de expansión confirmada.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>2. TIER B SURGICAL (Convicción Estándar / Quirúrgica):</b> Asignado cuando el setup satisface las condiciones "
            "estructurales mínimas (Score de 65 a 79 puntos), pero la evidencia del Sistema 1 es moderada o mixta (evidencia 3/5). "
            "La compuerta elástica reduce automáticamente la exposición al 50% del riesgo unitario (0.50x), restringe el Take-Profit "
            "a un ratio de 1.50R y programa el aseguramiento preventivo a Break-Even de forma temprana al alcanzar +0.80R.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>3. TIER C VETO (Rechazo Preventivo de Trampas de Liquidez):</b> Si el Sistema 1 detecta discrepancias con "
            "la macroestructura, clasifica el setup como <code>potential_trap</code> o <code>contradictory</code>, o el Score "
            "es inferior a 65 puntos, se activa el veto automático. La orden se descarta en memoria con riesgo 0%, evitando "
            "operar en zonas de absorción contraria o manipulación institucional.",
            apa_p,
        )
    )

    # TABLA 3: Matriz de Elastic Gating
    story.append(Spacer(1, 8))
    story.append(Paragraph("Tabla 3", table_num_style))
    story.append(Paragraph("<i>Matriz de Decisión y Compuertas Elásticas de Riesgo (Elastic Gating)</i>", table_title_style))

    table_data_3 = [
        [
            Paragraph("<b>Nivel de Ejecución</b>", table_cell_bold),
            Paragraph("<b>Criterios Sistema 1 &amp; 2</b>", table_cell_bold),
            Paragraph("<b>Asignación de Riesgo</b>", table_cell_bold),
            Paragraph("<b>Target R:R &amp; Gestión</b>", table_cell_bold),
        ],
        [
            Paragraph("<code>TIER_A_ALPHA</code>", table_cell_style),
            Paragraph("Score &ge; 80, Sistema 1: <i>Aligned</i>, Evidencia &ge; 4, <i>Genuine Expansion</i>.", table_cell_style),
            Paragraph("100% de la unidad de riesgo base (1.0x).", table_cell_style),
            Paragraph("Ratio 2.2R a 2.5R. Trailing stop dinámico por swing.", table_cell_style),
        ],
        [
            Paragraph("<code>TIER_B_SURGICAL</code>", table_cell_style),
            Paragraph("Score 65–79, Sistema 1: <i>Mixed / Aligned</i>, Evidencia = 3.", table_cell_style),
            Paragraph("50% de la unidad de riesgo base (0.50x).", table_cell_style),
            Paragraph("Ratio 1.50R acotado. Break-even anticipado en +0.80R.", table_cell_style),
        ],
        [
            Paragraph("<code>TIER_C_VETO</code>", table_cell_style),
            Paragraph("Score &lt; 65 o Sistema 1: <i>Contradictory / Potential Trap</i>.", table_cell_style),
            Paragraph("0.0% (Bloqueo absoluto de ejecución).", table_cell_style),
            Paragraph("Orden descartada en memoria. Registro en journal de auditoría.", table_cell_style),
        ],
    ]

    t3 = Table(table_data_3, colWidths=[100, 160, 110, 100])
    t3.setStyle(
        TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
            ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.append(t3)
    story.append(
        Paragraph(
            "<i>Nota.</i> Arquitectura de modulación de riesgo dinámico implementada en <code>strategy.synthetic_system_one</code>.",
            table_note_style,
        )
    )

    story.append(Paragraph("Protección Estructural y Salida por Decaimiento Temporal (Time-Decay Exit)", heading_2))

    story.append(
        Paragraph(
            "En índices sintéticos con microestructura de saltos (Boom y Crash de Deriv), donde el precio experimenta "
            "un deslizamiento constante (<i>drift</i>) tick-a-tick en un sentido y saltos impulsivos en el opuesto, la "
            "variable temporal reviste una importancia crítica. El análisis forense de operaciones pasadas reveló "
            "que perseguir saltos tras su ocurrencia (patrón denominado <code>JUMP_CLUSTER_CONTINUATION</code>) inducía "
            "compras perjudiciales en la cresta máxima de los picos alcistas de Boom o ventas en el piso de Crash.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "La arquitectura actual eliminó de raíz esta vulnerabilidad mediante dos defensas complementarias: "
            "(a) <i>Entradas disciplinadas exclusivamente por retesteo de niveles de soporte o resistencia</i> con "
            "agotamiento comprobado de drift en oscilador RSI (<code>M5_RSI_EXHAUSTION</code>) y mecha de absorción "
            "verificada; y (b) <i>Evaluación de sostenimiento estructural (<code>is_structure_holding</code>) con salida "
            "por decaimiento temporal (Time-Decay Exit)</i>. Si transcurren entre 8 y 16 velas M5 (40 a 80 minutos) "
            "sin que ocurra la expansión proyectada, o si la deriva del precio supera una banda de tolerancia recalibrada "
            "a 1.50 ATR respecto al precio de entrada, el bot ejecuta un cierre inmediato por invalidación estructural, "
            "impidiendo que el drift continuo degrade el capital de la cuenta.",
            apa_p,
        )
    )

    story.append(Paragraph("Filtros Preventivos Multicapa de Mercado", heading_2))

    story.append(
        Paragraph(
            "Previamente a la activación de cualquier pipeline de Sistema 1 o 2, la orden candidata debe superar "
            "secuencialmente una batería de filtros deterministas independientes:",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>1. Filtro Macroeconómico de Noticias (<code>news_filter</code>):</b> Rastrea en tiempo real el calendario "
            "institucional de ForexFactory, suspendiendo la apertura de posiciones en una ventana de seguridad "
            "(15 minutos previos y 30 minutos posteriores a publicaciones de tasas de interés, IPC o empleo no agrícola).",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>2. Filtro de Fricción y Calidad de Mercado (<code>spread_filter</code> / <code>market_quality_filter</code>):</b> "
            "Evalúa el costo de horquilla (spread) en relación con la volatilidad ATR. Si las condiciones de liquidez son "
            "anómalas o el ruido supera el umbral de viabilidad matemática, la ejecución se bloquea.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "<b>3. Filtro de Preservación de Capital y Drawdown (<code>drawdown_filter</code>):</b> Actúa como un interruptor "
            "de corte automático (<i>Circuit Breaker</i>). Ante pérdidas intradía o acumuladas que alcancen los límites "
            "máximos fijados, el sistema inhabilita la toma de nuevas posiciones para salvaguardar el patrimonio.",
            apa_p,
        )
    )

    # =========================================================================
    # SECCIÓN 5: APRENDIZAJE CONTINUO Y MEMORIA COGNITIVA
    # =========================================================================
    story.append(Paragraph("Sistema de Aprendizaje Continuo y Memoria Cognitiva (Autonomous Learning Lab)", heading_1))

    story.append(
        Paragraph(
            "A diferencia de los robots convencionales cuyos parámetros permanecen estáticos hasta una reoptimización "
            "manual, Quantum implementa el <code>Autonomous Learning Lab</code>, un subsistema de plasticidad algorítmica "
            "compuesto por tres módulos integrados:",
            apa_p,
        )
    )

    story.append(Paragraph("Autopsias Cognitivas Post-Mortem de Trades (TradeAutopsyEngine)", heading_2))

    story.append(
        Paragraph(
            "Inmediatamente tras el cierre de cualquier operación en MetaTrader 5 (por Take-Profit, Stop-Loss, Trailing "
            "Stop o Time-Decay), el motor <code>TradeAutopsyEngine</code> realiza un análisis forense instantáneo. La autopsia "
            "calcula métricas de precisión de ejecución como la <i>Eficiencia de Salida</i> (porcentaje capturado del "
            "movimiento favorable máximo posible) y la calidad del timing (clasificado en <i>Óptimo</i>, <i>Ligeramente Tardío</i> "
            "o <i>Anticipado</i>).",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "Posteriormente, el motor estructura la lección bajo el marco de <b>Memoria Neural de 4 Pasos</b>: "
            "(1) <i>Qué pensé:</i> hipótesis técnica y confluencias registradas al momento de apertura; (2) <i>Qué ocurrió:</i> "
            "dinámica real del precio y eventos de volatilidad observados; (3) <i>Qué aprendí:</i> identificación de mechas de "
            "manipulación o desgaste tendencial; y (4) <i>Qué haré diferente:</i> ajuste adaptativo de umbrales para "
            "configuraciones análogas en el futuro.",
            apa_p,
        )
    )

    story.append(Paragraph("Banco de Memoria Empírica y Validación Fuera de Muestra (OOS)", heading_2))

    story.append(
        Paragraph(
            "Las conclusiones forenses se consolidan de manera persistente en el <code>MemoryBank</code>, el cual indexa "
            "los patrones empíricos por par y régimen de volatilidad, actualizando las probabilidades a priori (<i>Bayesian "
            "priors</i>) utilizadas por los filtros de decisión. Para evitar la degradación por sobreajuste (<i>curve fitting</i>), "
            "el motor <code>ValidationEngine</code> audita periódicamente la robustez estadística mediante pruebas estrictas fuera "
            "de muestra (<i>Out-of-Sample</i>, OOS) y esquemas <i>Walk-Forward</i>, garantizando que ninguna adaptación sea "
            "incorporada a la operativa si no demuestra superioridad empírica verificable.",
            apa_p,
        )
    )

    # =========================================================================
    # SECCIÓN 6: FLOTA MULTIACTIVO Y ESPECIALIZACIÓN DE BOTS
    # =========================================================================
    story.append(Paragraph("Flota Multiactivo y Especialización Táctica de Bots", heading_1))

    story.append(
        Paragraph(
            "Con el fin de mitigar la correlación de cartera y maximizar el ratio de Sharpe global, Quantum opera a través "
            "de una flota de agentes autónomos concurrentes supervisados las 24 horas del día por el proceso <code>run_demo_bots.py</code> "
            "(ver Tabla 4).",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "Cada bot responde a un perfil táctico rigurosamente delimitado: los Bots A, B, C y D cubren el mercado de Oro "
            "(XAUUSD) desde estrategias de alta frecuencia hasta perfiles de francotirador selectivo con umbral ML superior "
            "a 0.70; el Bot E explota la dinámica macroeconómica en USDJPY (M15 con confirmación H1); el Bot G implementa "
            "la estrategia pura Alchemist (MSNR + CRT); y el Bot Deriv opera de forma independiente y paralela la cesta de "
            "índices sintéticos (Boom 500, Crash 500, Volatility 75, Volatility 100). El supervisor monitoriza la salud de "
            "cada proceso hijo, reiniciando automáticamente cualquier instancia en caso de desconexión del broker y manteniendo "
            "la telemetría sincronizada en <code>fleet_runtime.json</code>.",
            apa_p,
        )
    )

    # TABLA 4: Especialización de la Flota
    story.append(Spacer(1, 8))
    story.append(Paragraph("Tabla 4", table_num_style))
    story.append(Paragraph("<i>Especialización Táctica de la Flota de Bots Autónomos (Flotas 1 y 2)</i>", table_title_style))

    table_data_4 = [
        [
            Paragraph("<b>Identificador</b>", table_cell_bold),
            Paragraph("<b>Activo &amp; Temporalidad</b>", table_cell_bold),
            Paragraph("<b>Enfoque Táctico / Modelo de IA</b>", table_cell_bold),
            Paragraph("<b>Perfil de Riesgo</b>", table_cell_bold),
        ],
        [
            Paragraph("<b>Bot A / A2</b>", table_cell_style),
            Paragraph("XAUUSD (Oro) &middot; M5 / M15", table_cell_style),
            Paragraph("Alta Frecuencia &middot; Umbral ML &ge; 0.45 &middot; Confluencia Rápida.", table_cell_style),
            Paragraph("Moderado (1.0% base).", table_cell_style),
        ],
        [
            Paragraph("<b>Bot B / B2</b>", table_cell_style),
            Paragraph("XAUUSD (Oro) &middot; M5 / M15", table_cell_style),
            Paragraph("Máximo Rendimiento &middot; Filtro ML &ge; 0.55 &middot; Tendencia Fuerte.", table_cell_style),
            Paragraph("Agresivo controlado (1.0%).", table_cell_style),
        ],
        [
            Paragraph("<b>Bot C / C2</b>", table_cell_style),
            Paragraph("XAUUSD (Oro) &middot; M5 / H1", table_cell_style),
            Paragraph("Francotirador Sniper &middot; Umbral ML &ge; 0.70 &middot; Alta Convicción.", table_cell_style),
            Paragraph("Conservador (0.50% a 1.0%).", table_cell_style),
        ],
        [
            Paragraph("<b>Bot D / D2</b>", table_cell_style),
            Paragraph("XAUUSD (Oro) &middot; M5 / M15", table_cell_style),
            Paragraph("Control de Estancamiento &middot; Reglas Puramente Deterministas.", table_cell_style),
            Paragraph("Defensivo (0.50%).", table_cell_style),
        ],
        [
            Paragraph("<b>Bot E</b>", table_cell_style),
            Paragraph("USDJPY &middot; M15 / H1", table_cell_style),
            Paragraph("Intraday Macro &middot; Narrativa Multi-sesión &middot; Kev Qwen Reasoner.", table_cell_style),
            Paragraph("Estricto (0.25% riesgo).", table_cell_style),
        ],
        [
            Paragraph("<b>Bot F</b>", table_cell_style),
            Paragraph("XAUUSD (Oro) &middot; M5", table_cell_style),
            Paragraph("Permisivo experimental &middot; Barridos 0.3 ATR &middot; Shadow ML.", table_cell_style),
            Paragraph("Experimental (0.50%).", table_cell_style),
        ],
        [
            Paragraph("<b>Bot G</b>", table_cell_style),
            Paragraph("XAUUSD (Oro) &middot; M5 / M15", table_cell_style),
            Paragraph("Alchemist Puro &middot; Soportes Malasios (MSNR) + CRT 9:00 AM NY.", table_cell_style),
            Paragraph("Quirúrgico (1.0%).", table_cell_style),
        ],
        [
            Paragraph("<b>Bot Deriv</b>", table_cell_style),
            Paragraph("Sintéticos (Vol75, Vol100, Boom/Crash 500)", table_cell_style),
            Paragraph("Spike Sniper &amp; Volatility SMC &middot; Inferencia Batch NanoJev PyTorch.", table_cell_style),
            Paragraph("Asimétrico adaptativo.", table_cell_style),
        ],
    ]

    t4 = Table(table_data_4, colWidths=[80, 110, 190, 90])
    t4.setStyle(
        TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
            ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.append(t4)
    story.append(
        Paragraph(
            "<i>Nota.</i> Estructura de despliegue concurrente supervisada en <code>run_demo_bots.py</code>.",
            table_note_style,
        )
    )

    # =========================================================================
    # SECCIÓN 7: DISCUSIÓN Y CONCLUSIONES
    # =========================================================================
    story.append(PageBreak())
    story.append(Paragraph("Discusión y Conclusiones", heading_1))

    story.append(
        Paragraph(
            "La arquitectura de inteligencia artificial, aprendizaje automático y gobernanza de decisiones "
            "implementada en Quantum Trading Bot evidencia que la superioridad operativa en los mercados financieros "
            "contemporáneos no radica en la mera acumulación indiscriminada de parámetros en una red neuronal "
            "aislada, sino en la coherencia epistemológica de la arquitectura global. Al estructurar el flujo computacional "
            "en torno a la Teoría del Proceso Dual de Daniel Kahneman, la plataforma resuelve la histórica tensión entre "
            "la velocidad heurística de inferencia (Sistema 1) y el rigor metodológico de la validación estructural, los "
            "conceptos de dinero inteligente (SMC) y la gestión matemática del riesgo (Sistema 2).",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "Un hallazgo central derivado de la calibración del sistema es la necesidad de neutralizar el sesgo de "
            "persecución en regímenes de alta asimetría. La investigación empírica en activos sintéticos con dinámicas de "
            "saltos discretos (serie Boom y Crash) demostró que intentar capturar continuaciones inmediatas tras un salto "
            "(<i>jump clustering</i>) inducía entradas en los máximos de las oscilaciones, provocando un desgaste irreversible "
            "por deriva temporal. La imposición estricta de filtros de agotamiento en osciladores (<code>M5_RSI_EXHAUSTION</code>), "
            "mechas de absorción/rechazo en soportes y resistencias institucionales y salidas preventivas por decaimiento "
            "temporal (<i>Time-Decay Exit</i>) con tolerancias dinámicas de 1.50 ATR transformó una microestructura adversa "
            "en una fuente de alfa sostenible y verificable.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "Asimismo, el ecosistema descarta cualquier práctica de recuperación especulativa descontrolada —como "
            "martingalas, grillas abiertas, promediaciones de pérdidas a la baja o dilataciones injustificadas del tiempo "
            "de exposición—, las cuales suelen enmascarar sistemas de expectativa matemática negativa. Por el contrario, "
            "Quantum implementa una disciplina cuantitativa inquebrantable basada en compuertas elásticas de riesgo "
            "(<i>Elastic Gating</i>), ratios asimétricos (2.2R a 2.5R en Nivel Alpha), proyección estocástica de trayectorias "
            "mediante el oráculo transformador <i>Kronos</i> y un ciclo cerrado de autopsias cognitivas post-mortem con "
            "actualización bayesiana de priors en el <code>Autonomous Learning Lab</code>.",
            apa_p,
        )
    )

    story.append(
        Paragraph(
            "En conclusión, Quantum Trading Bot se consolida como un estándar científico avanzado en trading algorítmico. "
            "La plataforma demuestra empíricamente que la inteligencia artificial financiera alcanza su máximo potencial "
            "cuando se subordina de manera estricta a principios de trazabilidad lógica, preservación de capital, modularidad "
            "multiagente y validación rigurosa fuera de muestra.",
            apa_p,
        )
    )

    # =========================================================================
    # SECCIÓN 8: REFERENCIAS BIBLIOGRÁFICAS (FORMATO APA 7MA)
    # =========================================================================
    story.append(PageBreak())
    story.append(Paragraph("Referencias", heading_1))

    referencias = [
        "Breiman, L. (2001). Random forests. <i>Machine Learning</i>, 45(1), 5–32. https://doi.org/10.1023/A:1010933404324",
        "Cartea, Á., Jaimungal, S., &amp; Penalva, J. (2015). <i>Algorithmic and high-frequency trading</i>. Cambridge University Press. https://doi.org/10.1017/CBO9781139656894",
        "Chen, T., &amp; Guestrin, C. (2016). XGBoost: A scalable tree boosting system. En <i>Proceedings of the 22nd ACM SIGKDD International Conference on Knowledge Discovery and Data Mining</i> (pp. 785–794). ACM. https://doi.org/10.1145/2939672.2945358",
        "Cho, K., van Merriënboer, B., Gulcehre, C., Bahdanau, D., Bougares, F., Schwenk, H., &amp; Bengio, Y. (2014). Learning phrase representations using RNN encoder-decoder for statistical machine translation. <i>arXiv preprint arXiv:1406.1078</i>. https://doi.org/10.48550/arXiv.1406.1078",
        "Hochreiter, S., &amp; Schmidhuber, J. (1997). Long short-term memory. <i>Neural Computation</i>, 9(8), 1735–1780. https://doi.org/10.1162/neco.1997.9.8.1735",
        "Kahneman, D. (2011). <i>Thinking, fast and slow</i>. Farrar, Straus and Giroux.",
        "Ke, G., Meng, Q., Finley, T., Wang, T., Chen, W., Ma, W., Ye, Q., &amp; Liu, T. Y. (2017). LightGBM: A highly efficient gradient boosting decision tree. <i>Advances in Neural Information Processing Systems</i>, 30, 3146–3154.",
        "López de Prado, M. (2018). <i>Advances in financial machine learning</i>. John Wiley &amp; Sons. https://doi.org/10.1002/9781119482116",
        "Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A. N., Kaiser, Ł., &amp; Polosukhin, I. (2017). Attention is all you need. <i>Advances in Neural Information Processing Systems</i>, 30, 5998–6008.",
    ]

    for ref in referencias:
        story.append(Paragraph(ref, ref_style))

    # Compilación del documento con foliación y encabezados APA 7ma
    doc.build(story, canvasmaker=APANumberedCanvas)
    print(f"[OK] Documento PDF compilado con éxito en: {filename}")


if __name__ == "__main__":
    out_pdf = "docs/Arquitectura_IA_ML_Quantum_Trading_Bot_APA.pdf"
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    build_pdf(out_pdf)

    # Copia en la raíz para acceso inmediato
    root_pdf = "Arquitectura_IA_ML_Quantum_Trading_Bot_APA.pdf"
    shutil.copyfile(out_pdf, root_pdf)
    print(f"[OK] Copia sincronizada en la raíz del proyecto: {root_pdf}")
