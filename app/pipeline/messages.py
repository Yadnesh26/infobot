"""Fixed, human-reviewable user-facing text in English, Hindi and Marathi.

Anything a model writes goes through guard.sanitize_output; anything here is
static, so it can be reviewed and edited like copy. Safety-critical messages
(the medical hard stop, the injection refusal) live here on purpose: they must
never be model-generated.
"""

LANGS = ("en", "hi", "mr")


def pick(table: dict[str, str], lang: str | None) -> str:
    return table.get((lang or "en").split("-")[0].lower(), table["en"])


def all_langs(table: dict[str, str]) -> str:
    """For when we don't know the user's language: all three, stacked."""
    return "\n\n".join(table[lang] for lang in LANGS)


CAPABILITY = {
    "en": "Forward me a message, screenshot, voice note or video that makes a claim, and I'll fact-check it.",
    "hi": "कोई दावा करने वाला मैसेज, स्क्रीनशॉट, वॉइस नोट या वीडियो मुझे फॉरवर्ड करें, मैं उसकी जाँच करूँगा।",
    "mr": "दावा करणारा मेसेज, स्क्रीनशॉट, व्हॉइस नोट किंवा व्हिडिओ मला फॉरवर्ड करा, मी त्याची तपासणी करेन.",
}

# Used only when the model gave no usable reply for a non-claim message.
NOT_A_CLAIM_FALLBACK = {
    "en": "I fact-check claims in forwarded messages, and I couldn't find a checkable claim in this one.",
    "hi": "मैं फॉरवर्ड किए गए मैसेज के दावों की जाँच करता हूँ, और इसमें मुझे जाँचने लायक कोई दावा नहीं मिला।",
    "mr": "मी फॉरवर्ड केलेल्या मेसेजमधील दाव्यांची तपासणी करतो, आणि यात तपासण्यासारखा दावा मला आढळला नाही.",
}

UNCLEAR = {
    "en": "I couldn't pick out a specific claim to check in that. Could you send the exact message you want checked, or say the claim in one sentence?",
    "hi": "मुझे इसमें जाँचने लायक कोई खास दावा समझ नहीं आया। कृपया वही मैसेज भेजें जिसकी जाँच करानी है, या दावा एक वाक्य में लिखें।",
    "mr": "यात तपासण्यासारखा नेमका दावा मला समजला नाही. कृपया ज्याची तपासणी करायची तोच मेसेज पाठवा किंवा दावा एका वाक्यात लिहा.",
}

BLOCKED = {
    "en": "I can only fact-check claims, and I can't follow instructions written inside a message. If you want something checked, please send just the claim.",
    "hi": "मैं सिर्फ़ दावों की जाँच कर सकता हूँ, और मैसेज के अंदर लिखे निर्देशों का पालन नहीं कर सकता। जाँच के लिए सिर्फ़ दावा भेजें।",
    "mr": "मी फक्त दाव्यांची तपासणी करू शकतो, आणि मेसेजमध्ये लिहिलेल्या सूचनांचे पालन करू शकत नाही. तपासणीसाठी फक्त दावा पाठवा.",
}

MEDIA_AUTHENTICITY = {
    "en": "I can't tell whether a photo or video is genuine, or where and when it was taken — that needs tools I don't have. Tip: search the image on Google Lens or TinEye to see whether it is older than the message claims. If the message also states facts in words, send those and I'll check them.",
    "hi": "मैं यह नहीं बता सकता कि कोई फ़ोटो या वीडियो असली है या नहीं, या कहाँ और कब लिया गया — इसके लिए मेरे पास ज़रूरी टूल नहीं हैं। सुझाव: Google Lens या TinEye पर फ़ोटो खोजकर देखें कि वह मैसेज के दावे से पुरानी तो नहीं। अगर मैसेज में शब्दों में कोई तथ्य भी है, तो उसे भेजें, मैं जाँचूँगा।",
    "mr": "फोटो किंवा व्हिडिओ खरा आहे का, किंवा तो कुठे आणि कधी काढला गेला हे मी सांगू शकत नाही — त्यासाठी माझ्याकडे आवश्यक साधने नाहीत. सूचना: Google Lens किंवा TinEye वर फोटो शोधून तो मेसेजच्या दाव्यापेक्षा जुना आहे का ते पाहा. मेसेजमध्ये शब्दांत काही तथ्ये असतील तर ती पाठवा, मी तपासेन.",
}

UNREADABLE = {
    "image": {
        "en": "I couldn't read any text in that image. If it carries a claim, please type it out or send a clearer screenshot.",
        "hi": "इस इमेज में मुझे कोई पढ़ने लायक टेक्स्ट नहीं मिला। अगर इसमें कोई दावा है, तो उसे लिखकर भेजें या साफ़ स्क्रीनशॉट भेजें।",
        "mr": "या इमेजमध्ये वाचता येईल असा मजकूर मला सापडला नाही. यात काही दावा असेल तर तो लिहून पाठवा किंवा स्पष्ट स्क्रीनशॉट पाठवा.",
    },
    "audio": {
        "en": "I couldn't make out any speech in that audio. Please send a clearer voice note, or type the claim.",
        "hi": "इस ऑडियो में मुझे कोई साफ़ आवाज़ सुनाई नहीं दी। कृपया साफ़ वॉइस नोट भेजें या दावा लिखकर भेजें।",
        "mr": "या ऑडिओमध्ये मला स्पष्ट बोलणे ऐकू आले नाही. कृपया स्पष्ट व्हॉइस नोट पाठवा किंवा दावा लिहून पाठवा.",
    },
    "video": {
        "en": "I couldn't make out any speech in that video. If it carries a claim, please type it out or send a clearer clip.",
        "hi": "इस वीडियो में मुझे कोई साफ़ आवाज़ सुनाई नहीं दी। अगर इसमें कोई दावा है, तो उसे लिखकर भेजें या साफ़ क्लिप भेजें।",
        "mr": "या व्हिडिओमध्ये मला स्पष्ट बोलणे ऐकू आले नाही. यात काही दावा असेल तर तो लिहून पाठवा किंवा स्पष्ट क्लिप पाठवा.",
    },
}

SEEN_IN_PHOTO = "I can see what looks like: {description}."

MULTI_HEADER = {
    "en": "I found {n} claims in your message. Here's what I checked:",
    "hi": "आपके मैसेज में {n} दावे मिले। मैंने ये जाँचा:",
    "mr": "तुमच्या मेसेजमध्ये {n} दावे आढळले. मी हे तपासले:",
}

MULTI_OMITTED = {
    "en": "There were more claims than I can check at once. Send the others separately and I'll check them too.",
    "hi": "इसमें इतने दावे थे कि मैं एक साथ सब नहीं जाँच सकता। बाकी अलग से भेजें, मैं उन्हें भी जाँचूँगा।",
    "mr": "यात इतके दावे होते की मी ते सर्व एकदम तपासू शकत नाही. उरलेले वेगळे पाठवा, मी तेही तपासेन.",
}

MEDICAL_SHORT = {
    "en": "⚕️ This one is about a personal medical situation, so I won't answer it here. Please ask a doctor or pharmacist (India health helpline: 104).",
    "hi": "⚕️ यह किसी की निजी स्वास्थ्य स्थिति से जुड़ा है, इसलिए मैं यहाँ जवाब नहीं दूँगा। कृपया डॉक्टर या फार्मासिस्ट से पूछें (भारत स्वास्थ्य हेल्पलाइन: 104)।",
    "mr": "⚕️ हे वैयक्तिक वैद्यकीय स्थितीशी संबंधित आहे, म्हणून मी येथे उत्तर देणार नाही. कृपया डॉक्टर किंवा फार्मासिस्टला विचारा (भारत आरोग्य हेल्पलाइन: 104).",
}

NO_SOURCES = {
    "en": "I couldn't find reliable sources to confirm or deny this claim. Please don't forward it until it has been checked.",
    "hi": "इस दावे की पुष्टि या खंडन करने वाले भरोसेमंद स्रोत मुझे नहीं मिले। जाँच होने तक कृपया इसे आगे न भेजें।",
    "mr": "या दाव्याची पुष्टी किंवा खंडन करणारे विश्वासार्ह स्रोत मला सापडले नाहीत. तपासणी होईपर्यंत कृपया हा संदेश पुढे पाठवू नका.",
}

GENERAL_KNOWLEDGE_NOTE = {
    "en": "(from general knowledge, no sources searched)",
    "hi": "(सामान्य जानकारी के आधार पर, स्रोत नहीं खोजे गए)",
    "mr": "(सामान्य माहितीवर आधारित, स्रोत शोधले नाहीत)",
}

BUSY = {
    "en": "I'm a bit overloaded right now and couldn't finish checking this. Please try again in a few minutes.",
    "hi": "अभी मुझ पर बहुत लोड है और मैं इसकी जाँच पूरी नहीं कर सका। कृपया कुछ मिनट बाद फिर कोशिश करें।",
    "mr": "सध्या माझ्यावर खूप ताण आहे आणि मी ही तपासणी पूर्ण करू शकलो नाही. कृपया काही मिनिटांनी पुन्हा प्रयत्न करा.",
}


TOO_LONG = {
    "audio": {
        "en": "That voice note is too long for me to check (limit: 3 minutes). Please send a shorter clip, or type the claim.",
        "hi": "यह वॉइस नोट मेरे लिए बहुत लंबा है (सीमा: 3 मिनट)। कृपया छोटा क्लिप भेजें या दावा लिखकर भेजें।",
        "mr": "हा व्हॉइस नोट माझ्यासाठी खूप मोठा आहे (मर्यादा: ३ मिनिटे). कृपया लहान क्लिप पाठवा किंवा दावा लिहून पाठवा.",
    },
    "video": {
        "en": "That video is too long or too large for me to check (limit: 3 minutes). Please send a shorter clip, or type the claim.",
        "hi": "यह वीडियो मेरे लिए बहुत लंबा या बड़ा है (सीमा: 3 मिनट)। कृपया छोटी क्लिप भेजें या दावा लिखकर भेजें।",
        "mr": "हा व्हिडिओ माझ्यासाठी खूप मोठा आहे (मर्यादा: ३ मिनिटे). कृपया लहान क्लिप पाठवा किंवा दावा लिहून पाठवा.",
    },
}


# ---------------------------------------------------------------------------
# Per-kind replies for messages with nothing to check. The model normally
# writes a reply about the specific message (see extract_classify.txt); these
# are the floor for when it leaves that empty, so a greeting is never told
# "no checkable claim found".
# ---------------------------------------------------------------------------

GREETING = {
    "en": "Hello! 👋 I'm InfoBot. I check messages that are going around on WhatsApp and tell you whether they hold up.",
    "hi": "नमस्ते! 👋 मैं InfoBot हूँ। WhatsApp पर घूम रहे मैसेज की जाँच करके बताता हूँ कि वे सही हैं या नहीं।",
    "mr": "नमस्कार! 👋 मी InfoBot आहे. WhatsApp वर फिरणारे मेसेज तपासून ते खरे आहेत की नाही ते सांगतो.",
}

ABOUT_BOT = {
    "en": "I'm InfoBot, a fact-checking assistant. Send me a forwarded message, screenshot, voice note or short video and I'll check the claims in it, tell you how sure I am, and show my sources. I can't give medical advice, and I can't tell whether a photo or video is genuine.",
    "hi": "मैं InfoBot हूँ, एक फ़ैक्ट-चेक असिस्टेंट। कोई फॉरवर्ड मैसेज, स्क्रीनशॉट, वॉइस नोट या छोटा वीडियो भेजें, मैं उसके दावों की जाँच करूँगा, बताऊँगा कि मुझे कितना भरोसा है, और स्रोत दिखाऊँगा। मैं चिकित्सा सलाह नहीं दे सकता, और यह नहीं बता सकता कि कोई फ़ोटो या वीडियो असली है या नहीं।",
    "mr": "मी InfoBot आहे, एक फॅक्ट-चेक असिस्टंट. फॉरवर्ड केलेला मेसेज, स्क्रीनशॉट, व्हॉइस नोट किंवा लहान व्हिडिओ पाठवा; मी त्यातील दाव्यांची तपासणी करेन, किती खात्री आहे ते सांगेन आणि स्रोत दाखवेन. मी वैद्यकीय सल्ला देऊ शकत नाही, आणि फोटो किंवा व्हिडिओ खरा आहे का ते सांगू शकत नाही.",
}

OPINION = {
    "en": "That's an opinion or a prediction, so there's nothing I can verify. If someone is claiming a fact about it, like a record, a number or an announcement, send me that and I'll check it.",
    "hi": "यह एक राय या भविष्यवाणी है, इसलिए इसमें मेरे जाँचने लायक कुछ नहीं है। अगर कोई इस बारे में कोई तथ्य बता रहा है, जैसे रिकॉर्ड, आँकड़ा या घोषणा, तो वह मुझे भेजें, मैं जाँचूँगा।",
    "mr": "हे मत किंवा भविष्यवाणी आहे, त्यामुळे मी पडताळू शकेन असे यात काही नाही. याबद्दल कोणी एखादे तथ्य सांगत असेल, जसे विक्रम, आकडा किंवा घोषणा, तर ते मला पाठवा, मी तपासेन.",
}

PRIVATE = {
    "en": "Thanks for telling me. That's a personal matter, and there's no public source I could check it against. If a rumour about it is going around as a forwarded message, send me that and I'll look into it.",
    "hi": "बताने के लिए धन्यवाद। यह एक निजी बात है, और इसे जाँचने के लिए कोई सार्वजनिक स्रोत नहीं है। अगर इस बारे में कोई अफ़वाह फॉरवर्ड मैसेज के रूप में घूम रही है, तो वह मुझे भेजें, मैं देखूँगा।",
    "mr": "सांगितल्याबद्दल धन्यवाद. ही वैयक्तिक बाब आहे आणि ती तपासण्यासाठी कोणताही सार्वजनिक स्रोत नाही. याबद्दल एखादी अफवा फॉरवर्ड मेसेज म्हणून फिरत असेल तर ती मला पाठवा, मी पाहीन.",
}

OUT_OF_SCOPE = {
    "en": "That's outside what I do: I only fact-check claims in messages, so I can't help with that one. If there's a message or claim you're unsure about, send it over and I'll check it.",
    "hi": "यह मेरे काम के दायरे से बाहर है: मैं सिर्फ़ मैसेज के दावों की जाँच करता हूँ, इसलिए इसमें मदद नहीं कर सकता। अगर कोई मैसेज या दावा आपको संदिग्ध लगे, तो भेजें, मैं जाँचूँगा।",
    "mr": "हे माझ्या कामाच्या कक्षेबाहेर आहे: मी फक्त मेसेजमधील दाव्यांची तपासणी करतो, त्यामुळे यात मदत करू शकत नाही. एखादा मेसेज किंवा दावा संशयास्पद वाटला तर पाठवा, मी तपासेन.",
}

KIND_FALLBACK = {
    "greeting": GREETING,
    "question_about_bot": ABOUT_BOT,
    "opinion_or_prediction": OPINION,
    "personal_or_private": PRIVATE,
    "out_of_scope_request": OUT_OF_SCOPE,
}

# ---------------------------------------------------------------------------
# Labels inside a verdict reply, so a Hindi or Marathi speaker isn't handed an
# English frame around a translated explanation.
# ---------------------------------------------------------------------------

VERDICT_HEADER = {"en": "Verdict", "hi": "निष्कर्ष", "mr": "निष्कर्ष"}
CONFIDENCE_WORD = {"en": "Confidence", "hi": "भरोसा", "mr": "विश्वासार्हता"}
SOURCES_WORD = {"en": "Sources", "hi": "स्रोत", "mr": "स्रोत"}

VERDICT_LABEL = {
    "true": {"en": "True", "hi": "सही", "mr": "खरे"},
    "false": {"en": "False", "hi": "गलत", "mr": "खोटे"},
    "misleading": {"en": "Misleading", "hi": "भ्रामक", "mr": "दिशाभूल करणारे"},
    "unverifiable": {"en": "Unverifiable", "hi": "सत्यापित नहीं हो सका", "mr": "पडताळणी करता आली नाही"},
}
CONFIDENCE_LEVEL = {
    "High": {"en": "High", "hi": "उच्च", "mr": "उच्च"},
    "Medium": {"en": "Medium", "hi": "मध्यम", "mr": "मध्यम"},
    "Low": {"en": "Low", "hi": "कम", "mr": "कमी"},
}

FOOTER = {"en": "— Verified by InfoBot", "hi": "— InfoBot द्वारा जाँचा गया", "mr": "— InfoBot ने तपासले"}
FOOTER_GENERAL = {
    "en": "— Verified by InfoBot (general-knowledge check only, no sources searched yet)",
    "hi": "— InfoBot द्वारा जाँचा गया (सिर्फ़ सामान्य जानकारी के आधार पर, अभी स्रोत नहीं खोजे गए)",
    "mr": "— InfoBot ने तपासले (फक्त सामान्य माहितीवर आधारित, अजून स्रोत शोधले नाहीत)",
}
FOOTER_GUIDANCE = {
    "en": "— InfoBot (general guidance only, not a verdict)",
    "hi": "— InfoBot (सिर्फ़ सामान्य जानकारी, कोई निर्णय नहीं)",
    "mr": "— InfoBot (फक्त सामान्य माहिती, निर्णय नव्हे)",
}
FORWARDED = {
    "en": "This message has been forwarded many times.",
    "hi": "यह मैसेज कई बार फॉरवर्ड किया जा चुका है।",
    "mr": "हा मेसेज अनेकदा फॉरवर्ड केला गेला आहे.",
}
