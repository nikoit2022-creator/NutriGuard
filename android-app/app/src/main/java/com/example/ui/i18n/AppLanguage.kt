package com.example.ui.i18n

import androidx.compose.material3.LocalTextStyle
import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit

enum class AppLanguage(val code: String) {
    ENGLISH("en"),
    BULGARIAN("bg");

    companion object {
        fun fromCode(code: String?): AppLanguage =
            entries.firstOrNull { it.code == code } ?: ENGLISH
    }
}

val LocalAppLanguage = staticCompositionLocalOf { AppLanguage.ENGLISH }

private val bulgarianUiText = mapOf(
    "Scan" to "Сканиране",
    "History" to "История",
    "Profile" to "Профил",
    "Back" to "Назад",
    "Close" to "Затвори",
    "Retry" to "Опитай отново",
    "Dismiss" to "Затвори",
    "Search" to "Търсене",
    "Clear search" to "Изчисти търсенето",
    "Error" to "Грешка",
    "Scanner" to "Скенер",
    "Search Ingredient Database" to "Търсене в базата със съставки",
    "Collapse" to "Свий",
    "Expand" to "Разгъни",
    "Hide sources" to "Скрий източниците",
    "Show sources" to "Покажи източниците",
    "Health Alerts" to "Здравни предупреждения",
    "Learn more" to "Научи повече",
    "Navigate" to "Отвори",
    "NutriGuard Shield" to "Щит NutriGuard",
    "Connecting to NutriGuard backend..." to "Свързване със сървъра на NutriGuard...",
    "Retry Connection" to "Опитай свързването отново",
    "Network Connection Failed" to "Неуспешна мрежова връзка",
    "Backend Connection Failed" to "Неуспешна връзка със сървъра",
    "Backend HTTP Error" to "HTTP грешка от сървъра",
    "Response Parsing Failed" to "Неуспешно прочитане на отговора",
    "Authentication Failed" to "Неуспешно удостоверяване",
    "Unable to reach the NutriGuard backend." to "Сървърът на NutriGuard не може да бъде достигнат.",
    "NutriGuard backend returned an unexpected auth response." to "Сървърът върна неочакван отговор при удостоверяване.",
    "NutriGuard device authentication failed." to "Удостоверяването на устройството е неуспешно.",
    "Failed to connect to NutriGuard backend." to "Свързването със сървъра на NutriGuard е неуспешно.",

    "NUTRIGUARD • INGREDIENT SCANNER" to "NUTRIGUARD • СКЕНЕР ЗА СЪСТАВКИ",
    "Scan a Product" to "Сканирай продукт",
    "Barcode" to "Баркод",
    "Ingredient Label" to "Етикет със съставки",
    "Scan Barcode" to "Сканирай баркод",
    "Scan Ingredient Label" to "Сканирай етикет",
    "Tap to Scan" to "Докосни за сканиране",
    "Scan barcode on any food packaging" to "Сканирай баркода върху опаковката",
    "Point camera at food ingredient list" to "Насочи камерата към списъка със съставки",
    "Open Gallery" to "Отвори галерията",
    "Manual Barcode or Text Lookup" to "Ръчно търсене по баркод или текст",
    "Type barcode number or paste ingredient list" to "Въведи баркод или постави списък със съставки",
    "Lookup" to "Търси",
    "Analyze Text" to "Анализирай текста",
    "Paste ingredients list (e.g. Water, Sugar, E951, E102)..." to "Постави списък със съставки (напр. вода, захар, E951, E102)...",
    "Sample Test Products" to "Примерни тестови продукти",
    "Searching product..." to "Търсене на продукта...",
    "Analyzing label photo..." to "Анализиране на снимката на етикета...",
    "Analyzing label…" to "Анализиране на етикета…",
    "Searching product…" to "Търсене на продукта…",
    "Reading nutrition and ingredient information from the photo" to "Разчитане на хранителните стойности и съставките от снимката",
    "Checking our database and trusted product sources" to "Проверка в нашата база и надеждни продуктови източници",
    "Label Scan Needed" to "Необходима е снимка на етикета",
    "Ingredients recognized" to "Разпознати съставки",
    "Loaded from saved product data" to "Заредено от запазените данни за продукта",
    "Processed by NutriGuard" to "Обработено от NutriGuard",
    "No ingredients available from this scan" to "Няма получени съставки от това сканиране",
    "The server returned no ingredients without a specific cause. You can try again." to "Сървърът не върна съставки и не посочи конкретна причина. Можеш да опиташ отново.",
    "Add another photo" to "Допълни със снимка",
    "A nutrition photo is optional and can help calculate a Health Score" to "По желание снимай хранителната таблица, за да помогнеш за изчисляване на здравната оценка",
    "Product identity is not confirmed yet" to "Самоличността на продукта още не е потвърдена",
    "Product name not confirmed" to "Името на продукта не е потвърдено",
    "We found useful information on the label" to "Открихме полезна информация на етикета",
    "One more scan will help complete the product" to "Още едно сканиране ще помогне да допълним продукта",
    "Nutrition information is still needed" to "Все още е необходима хранителната таблица",
    "Ingredient information is still needed" to "Все още е необходим списъкът със съставки",
    "Scan nutrition table" to "Снимай хранителната таблица",
    "Scan ingredient list" to "Снимай списъка със съставки",
    "Scan label for more information" to "Снимай етикета за повече информация",
    "Health Score pending • Scan the missing label information to complete it" to "Здравната оценка се изчислява • Снимай липсващата информация от етикета",
    "Couldn't Complete Lookup" to "Търсенето не можа да завърши",
    "No barcode value was detected." to "Не беше разпознат баркод.",
    "Unable to start barcode scanner." to "Баркод скенерът не може да бъде стартиран.",
    "Unable to read the selected image." to "Избраната снимка не може да бъде прочетена.",
    "Unable to process the selected image." to "Избраната снимка не може да бъде обработена.",
    "Unable to read the captured image." to "Заснетата снимка не може да бъде прочетена.",
    "Unable to process the captured image." to "Заснетата снимка не може да бъде обработена.",
    "Unable to open the camera." to "Камерата не може да бъде отворена.",
    "Unable to reach the server. Please check your connection." to "Сървърът не може да бъде достигнат. Провери връзката си.",
    "The request timed out. Please try again." to "Заявката изтече. Опитай отново.",
    "Your session could not be verified. Please restart the app and try again." to "Сесията не може да бъде потвърдена. Рестартирай приложението и опитай отново.",
    "Something went wrong. Please try again." to "Възникна проблем. Опитай отново.",
    "Something went wrong looking up this product. Please try again." to "Възникна проблем при търсенето на продукта. Опитай отново.",
    "Something went wrong analyzing this label. Please try again." to "Възникна проблем при анализа на етикета. Опитай отново.",
    "Something went wrong analyzing this text. Please try again." to "Възникна проблем при анализа на текста. Опитай отново.",

    "Scan History" to "История на сканиранията",
    "All products scanned and analyzed on this device" to "Всички продукти, сканирани и анализирани на това устройство",
    "Empty history" to "Празна история",
    "No Scanned Products Yet" to "Все още няма сканирани продукти",
    "Scan a barcode or photograph an ingredient label from the Scan tab to see your log." to "Сканирай баркод или снимай етикет от раздел Сканиране, за да видиш историята си.",
    "Start Scanning" to "Започни сканиране",
    "View Analysis" to "Виж анализа",

    "Analyzing Product Ingredients..." to "Анализиране на съставките...",
    "Evaluating additives against EFSA, FDA & WHO safety guidelines" to "Сравняване на добавките с насоките за безопасност на EFSA, FDA и WHO",
    "Analysis Unavailable" to "Анализът не е наличен",
    "Back to Scanner" to "Обратно към скенера",
    "Health Score" to "Здравна оценка",
    "Excellent" to "Отличен",
    "Good" to "Добър",
    "Moderate" to "Умерен",
    "Poor" to "Нисък",
    "Clean formulation & safe ingredients" to "Чист състав и безопасни съставки",
    "Balanced with minor processing" to "Балансиран продукт с умерена обработка",
    "Consume occasionally" to "Консумирай от време на време",
    "Highly processed or high-concern additives" to "Силно преработен или с рискови добавки",
    "out of 100" to "от 100",
    "NOVA Group" to "NOVA група",
    "Sugar" to "Захар",
    "Sodium" to "Натрий",
    "Saturated Fat" to "Наситени мазнини",
    "Saturated fat" to "Наситени мазнини",
    "Ultra-processed" to "Ултрапреработен",
    "Processed" to "Преработен",
    "Unprocessed" to "Непреработен",
    "per 100g" to "на 100 g",
    "Dietary Suitability" to "Хранителна пригодност",
    "Gluten-Free" to "Без глутен",
    "Lactose-Free" to "Без лактоза",
    "Vegan" to "Веган",
    "Vegetarian" to "Вегетариански",
    "Halal" to "Халал",
    "Kosher" to "Кошер",
    "Tap to view scientific profile" to "Докосни за научен профил",
    "Add more product information" to "Добави още информация за продукта",
    "Take another photo of ingredients, nutrition facts or product details" to "Направи допълнителна снимка на съставките, хранителната таблица или описанието",
    "Scanned Ingredient Text" to "Разпознат текст на съставките",
    "Verified from Food Safety Database" to "Проверено в базата за безопасност на храните",
    "Analyzed via NutriGuard Scientific Engine" to "Анализирано от научната система на NutriGuard",
    "No analysis available." to "Няма наличен анализ.",
    "Health Score Pending" to "Здравната оценка се изчислява",
    "Ingredients were recognized successfully. Scan the nutrition table to calculate a Health Score." to "Съставките са разпознати. Снимай хранителната таблица, за да изчислим здравната оценка.",
    "Nutrition data was found, but the ingredient list still needs a clearer scan." to "Хранителните стойности са намерени, но е необходима по-ясна снимка на съставките.",
    "This scan saved useful product data, but more label evidence is needed before a Health Score can be calculated." to "Сканирането запази полезна информация, но е необходима още информация от етикета за здравна оценка.",

    "Ingredient" to "Съставка",
    "Safe" to "Безопасна",
    "Potential concern" to "Възможен риск",
    "High concern" to "Висок риск",
    "Low concern" to "Нисък риск",
    "Limited data" to "Ограничени данни",
    "Use in moderation" to "Умерена употреба",
    "Ingredient details" to "Информация за съставката",
    "Description" to "Описание",
    "Purpose in food" to "Роля в продукта",
    "Health considerations" to "Въздействие върху здравето",
    "Why this rating" to "Причина за оценката",
    "Evidence" to "Научни данни",
    "Known side effects" to "Известни странични ефекти",
    "Allergens" to "Алергени",
    "Restricted in" to "Ограничена в",
    "Daily intake guidance" to "Насоки за дневен прием",
    "Source" to "Източник",
    "Sources" to "Източници",
    "Inspect ingredient" to "Виж информацията за съставката",
    "Open ingredient details" to "Отвори информацията за съставката",
    "No verified scientific profile available." to "Няма наличен потвърден научен профил.",
    "About this ingredient" to "За тази съставка",
    "Health and safety" to "Здраве и безопасност",
    "Evidence and regulation" to "Научни данни и регулации",
    "WHO / IARC classification" to "Класификация WHO / IARC",
    "Acceptable daily intake" to "Допустим дневен прием",
    "Source-backed summary" to "Обобщение от проверени източници",
    "Source verified · Human review pending" to "Източниците са проверени · Предстои човешки преглед",
    "Origin" to "Произход",
    "Function in food" to "Функция в храната",
    "What studies report" to "Какво съобщават проучванията",
    "Regulatory context" to "Регулаторен контекст",
    "Overview" to "Обобщение",
    "Recognized ingredient" to "Разпозната съставка",
    "Recognized ingredients" to "Разпознати съставки",
    "Tap an ingredient to view available details" to "Докосни съставка, за да видиш наличната информация",
    "Show less" to "Покажи по-малко",
    "Allergen" to "Алерген",

    "NOVA food processing" to "Степен на преработка NOVA",
    "What the four groups mean" to "Какво означават четирите групи",
    "Unprocessed or minimally processed" to "Непреработени или минимално преработени",
    "Processed culinary ingredients" to "Преработени кулинарни съставки",
    "Processed foods" to "Преработени храни",
    "Ultra-processed foods" to "Ултрапреработени храни",
    "Foods kept close to their natural state, with processing such as cleaning, freezing or pasteurizing." to "Храни, запазени близо до естественото им състояние, с обработка като почистване, замразяване или пастьоризация.",
    "Ingredients obtained from Group 1 foods or nature and mainly used for cooking, such as oils, butter, sugar and salt." to "Съставки, получени от храни от група 1 или от природата и използвани главно за готвене, като масла, масло, захар и сол.",
    "Group 1 foods combined with Group 2 ingredients, usually to improve preservation or taste." to "Храни от група 1, комбинирани със съставки от група 2, обикновено за по-добро съхранение или вкус.",
    "Industrial formulations that commonly contain several ingredients, additives or processes not normally used in home cooking." to "Промишлени формули, които обикновено съдържат много съставки, добавки или процеси, нетипични за домашното готвене.",
    "NOVA describes how and why a food was processed. It does not replace the nutrition facts, ingredient safety information or the overall Health Score." to "NOVA описва как и защо е преработена храната. Тя не замества хранителните стойности, безопасността на съставките или общата здравна оценка.",

    "Factor details" to "Информация за фактора",
    "Lower" to "По-ниско",
    "High" to "Високо",
    "No points deducted from Health Score" to "Не се отнемат точки от здравната оценка",
    "Possible health effects" to "Възможни ефекти върху здравето",
    "Daily guidance for adults" to "Дневни насоки за пълнолетни",
    "How to read this value" to "Как да разчиташ стойността",
    "Frequent high intake of free sugars can contribute to tooth decay, unhealthy weight gain and related metabolic risk." to "Честият висок прием на свободни захари може да допринесе за кариеси, нездравословно покачване на теглото и свързан метаболитен риск.",
    "WHO recommends keeping free sugars below 10% of daily energy (about 50 g for a 2,000 kcal diet), with below 5% or about 25 g offering additional benefits." to "WHO препоръчва свободните захари да са под 10% от дневната енергия (около 50 g при 2000 kcal), а под 5% или около 25 g носи допълнителни ползи.",
    "The label usually reports total sugars. The WHO limit applies to free sugars, so this value is a comparison guide, not an exact daily allowance for this product." to "Етикетът обикновено показва общите захари. Ограничението на WHO е за свободните захари, затова сравнението е ориентировъчно, а не точна дневна доза за продукта.",
    "Regular high sodium intake can raise blood pressure and increase cardiovascular and kidney health risk." to "Редовният висок прием на натрий може да повиши кръвното налягане и риска за сърдечносъдовото и бъбречното здраве.",
    "WHO recommends less than 2,000 mg of sodium per day for adults, equivalent to less than 5 g of salt." to "WHO препоръчва под 2000 mg натрий дневно за пълнолетни, което се равнява на под 5 g сол.",
    "This product value is shown per 100 g. Your actual intake depends on the portion consumed and sodium from the rest of the day." to "Стойността е за 100 g продукт. Реалният прием зависи от порцията и натрия от останалата храна през деня.",
    "High saturated-fat intake can raise LDL cholesterol and, over time, increase cardiovascular risk." to "Високият прием на наситени мазнини може да повиши LDL холестерола и с времето да увеличи сърдечносъдовия риск.",
    "WHO recommends that saturated fat provide no more than 10% of daily energy — about 22 g in a 2,000 kcal diet." to "WHO препоръчва наситените мазнини да дават не повече от 10% от дневната енергия — около 22 g при хранене от 2000 kcal.",
    "The gram estimate changes with individual energy needs. Prefer replacing saturated fat with unsaturated fats rather than adding more calories." to "Ориентировъчните грамове зависят от индивидуалните енергийни нужди. За предпочитане е наситените мазнини да се заменят с ненаситени.",
    "WHO — Sugars intake guidance" to "WHO — Насоки за прием на захари",
    "WHO — Sodium reduction" to "WHO — Намаляване на натрия",
    "WHO — Saturated fatty acid guideline" to "WHO — Насоки за наситените мазнини",

    "Personalized Health Alerts" to "Персонализирани здравни предупреждения",
    "Matched against your active health profile" to "Съобразено с активния ти здравен профил",
    "Health Profile" to "Здравен профил",
    "Customize alerts for your health needs and dietary preferences" to "Настрой предупрежденията според здравните си нужди и хранителни предпочитания",
    "Health Conditions & Life Stages" to "Здравни състояния и етапи от живота",
    "Dietary Preferences & Allergens" to "Хранителни предпочитания и алергени",
    "Diabetes & Glycemic Control" to "Диабет и гликемичен контрол",
    "Hypertension (High Blood Pressure)" to "Хипертония (високо кръвно налягане)",
    "Kidney & Renal Health" to "Бъбречно здраве",
    "Gout / High Uric Acid" to "Подагра / висока пикочна киселина",
    "Pregnancy & Lactation" to "Бременност и кърмене",
    "Children & Toddlers" to "Деца и малки деца",
    "Cardiovascular & High Cholesterol" to "Сърдечносъдово здраве и висок холестерол",
    "Alerts high sugar (>5g), maltodextrin, dextrose, & high GI sweeteners" to "Предупреждава за висока захар (>5 g), малтодекстрин, декстроза и подсладители с висок гликемичен индекс",
    "Alerts high sodium (>400mg), MSG (E621), & sodium preservers" to "Предупреждава за висок натрий (>400 mg), MSG (E621) и натриеви консерванти",
    "Alerts high sodium, potassium, & phosphate additives (E338-E343)" to "Предупреждава за високи нива на натрий, калий и фосфатни добавки (E338–E343)",
    "Alerts high fructose corn syrup, purine boosters, & yeast extracts" to "Предупреждава за глюкозо-фруктозен сироп, източници на пурини и екстракти от мая",
    "Alerts nitrates, nitrites, saccharin, titanium dioxide, & unpasteurized additives" to "Предупреждава за нитрати, нитрити, захарин, титанов диоксид и непастьоризирани съставки",
    "Alerts Southampton Six hyperactivity dyes (E102, E110, E129) & high sugar" to "Предупреждава за оцветители, свързвани с хиперактивност (E102, E110, E129), и висока захар",
    "Alerts saturated fats (>4g), palm oil, trans fats, & hydrogenated oils" to "Предупреждава за наситени мазнини (>4 g), палмово масло, трансмазнини и хидрогенирани масла",
    "Alerts wheat, barley, rye, or gluten cross-contact" to "Предупреждава за пшеница, ечемик, ръж или възможен контакт с глутен",
    "Alerts milk, whey, butter, or lactose derived ingredients" to "Предупреждава за мляко, суроватка, масло или съставки с лактоза",
    "Alerts animal-derived ingredients, gelatin, & carmine (E120)" to "Предупреждава за съставки от животински произход, желатин и кармин (E120)",
    "Alerts pork derivatives, alcohol carriers, & non-halal additives" to "Предупреждава за свински производни, алкохолни носители и нехалал добавки",
    "Alerts non-kosher processing agents or uncertified origins" to "Предупреждава за некошерни технологични агенти или непотвърден произход",
    "Halal Compliance" to "Халал съответствие",
    "Kosher Compliance" to "Кошер съответствие",
    "Scientific Ingredient Library" to "Научна библиотека на съставките",
    "Browse additive encyclopedia & safety studies" to "Разгледай енциклопедията на добавките и проучванията за безопасност",
    "Developer & Architecture Diagnostics" to "Диагностика за разработчици и архитектура",
    "Ingredient Library" to "Библиотека на съставките",
    "EFSA, FDA & WHO safety classifications" to "Класификации за безопасност на EFSA, FDA и WHO",
    "Search by E-number, name, or category..." to "Търси по E-номер, име или категория...",
    "Potential Concern" to "Възможен риск",
    "High Concern" to "Висок риск",
    "No ingredients matching your criteria" to "Няма съставки, отговарящи на критериите",

    "System Diagnostics" to "Системна диагностика",
    "Architecture & Production Specifications" to "Архитектура и производствени спецификации",
    "System Arch" to "Архитектура",
    "Schema" to "Схема",
    "API Spec" to "API спецификация",
    "Docker & Ops" to "Docker и операции",
    "Modular Full-Stack Architecture" to "Модулна full-stack архитектура",
    "Production Project Folder Structure" to "Структура на проекта",
    "PostgreSQL Database Schema" to "Схема на PostgreSQL базата",
    "REST API Specification (FastAPI / OpenAPI)" to "REST API спецификация (FastAPI / OpenAPI)",
    "Database & Cache Metrics" to "Показатели за базата и кеша",
    "Cached Products" to "Кеширани продукти",
    "Scans Logged" to "Записани сканирания",
    "Docker Deployment Configuration" to "Конфигурация за Docker внедряване"
)

fun localizeUiText(text: String, language: AppLanguage): String {
    if (language == AppLanguage.ENGLISH) return text
    bulgarianUiText[text]?.let { return it }

    return when {
        text.startsWith("Ingredients (") -> text.replaceFirst("Ingredients", "Съставки")
        text.startsWith("Recognized ingredients (") -> text.replaceFirst("Recognized ingredients", "Разпознати съставки")
        text.startsWith("Sources (") -> text.replaceFirst("Sources", "Източници")
        text.startsWith("Show all (") -> text.replaceFirst("Show all", "Покажи всички")
        text.startsWith("All (") -> text.replaceFirst("All", "Всички")
        text.startsWith("HISTORY •") -> text.replaceFirst("HISTORY", "ИСТОРИЯ")
        text.startsWith("This product is Group ") -> text.replaceFirst("This product is Group", "Този продукт е в група")
        text.startsWith("Group ") -> text.replaceFirst("Group", "Група")
        text.startsWith("Trigger factor: ") -> text.replaceFirst("Trigger factor:", "Причина:")
        text.startsWith("Health Score impact: ") -> text
            .replaceFirst("Health Score impact:", "Влияние върху здравната оценка:")
            .replace(" points", " точки")
        text.endsWith(" per 100 g") -> text.removeSuffix(" per 100 g") + " на 100 g"
        text.startsWith("✓ ") -> "✓ " + localizeUiText(text.removePrefix("✓ "), language)
        text.startsWith("✕ ") -> "✕ " + localizeUiText(text.removePrefix("✕ "), language)
        text.contains(": ") -> {
            val label = text.substringBefore(": ")
            val value = text.substringAfter(": ")
            val localizedLabel = bulgarianUiText[label] ?: label
            val localizedValue = bulgarianUiText[value] ?: value
            "$localizedLabel: $localizedValue"
        }
        text.endsWith(" not approved") -> text.removeSuffix(" not approved") + " — неодобрено"
        text.endsWith(" approved") -> text.removeSuffix(" approved") + " — одобрено"
        text.matches(Regex("\\d+ active filters? applied to scans")) -> {
            val count = text.substringBefore(' ')
            "$count активни филтъра при сканиране"
        }
        else -> text
    }
}

@Composable
fun LocalizedText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontSize: TextUnit = TextUnit.Unspecified,
    fontStyle: FontStyle? = null,
    fontWeight: FontWeight? = null,
    fontFamily: FontFamily? = null,
    letterSpacing: TextUnit = TextUnit.Unspecified,
    textDecoration: TextDecoration? = null,
    textAlign: TextAlign? = null,
    lineHeight: TextUnit = TextUnit.Unspecified,
    overflow: TextOverflow = TextOverflow.Clip,
    softWrap: Boolean = true,
    maxLines: Int = Int.MAX_VALUE,
    minLines: Int = 1,
    onTextLayout: (TextLayoutResult) -> Unit = {},
    style: TextStyle = LocalTextStyle.current
) {
    androidx.compose.material3.Text(
        text = localizeUiText(text, LocalAppLanguage.current),
        modifier = modifier,
        color = color,
        fontSize = fontSize,
        fontStyle = fontStyle,
        fontWeight = fontWeight,
        fontFamily = fontFamily,
        letterSpacing = letterSpacing,
        textDecoration = textDecoration,
        textAlign = textAlign,
        lineHeight = lineHeight,
        overflow = overflow,
        softWrap = softWrap,
        maxLines = maxLines,
        minLines = minLines,
        onTextLayout = onTextLayout,
        style = style
    )
}
