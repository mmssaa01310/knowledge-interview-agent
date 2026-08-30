import jaCommon from "./locales/ja-JP/common.json";
import jaNavigation from "./locales/ja-JP/navigation.json";
import jaKnowledge from "./locales/ja-JP/knowledge.json";
import jaInterview from "./locales/ja-JP/interview.json";
import jaSettings from "./locales/ja-JP/settings.json";
import jaErrors from "./locales/ja-JP/errors.json";
import jaValidation from "./locales/ja-JP/validation.json";
import jaDashboard from "./locales/ja-JP/dashboard.json";
import enCommon from "./locales/en-US/common.json";
import enNavigation from "./locales/en-US/navigation.json";
import enKnowledge from "./locales/en-US/knowledge.json";
import enInterview from "./locales/en-US/interview.json";
import enSettings from "./locales/en-US/settings.json";
import enErrors from "./locales/en-US/errors.json";
import enValidation from "./locales/en-US/validation.json";
import enDashboard from "./locales/en-US/dashboard.json";
import zhCommon from "./locales/zh-CN/common.json";
import zhNavigation from "./locales/zh-CN/navigation.json";
import zhKnowledge from "./locales/zh-CN/knowledge.json";
import zhInterview from "./locales/zh-CN/interview.json";
import zhSettings from "./locales/zh-CN/settings.json";
import zhErrors from "./locales/zh-CN/errors.json";
import zhValidation from "./locales/zh-CN/validation.json";
import zhDashboard from "./locales/zh-CN/dashboard.json";
import thCommon from "./locales/th-TH/common.json";
import thNavigation from "./locales/th-TH/navigation.json";
import thKnowledge from "./locales/th-TH/knowledge.json";
import thInterview from "./locales/th-TH/interview.json";
import thSettings from "./locales/th-TH/settings.json";
import thErrors from "./locales/th-TH/errors.json";
import thValidation from "./locales/th-TH/validation.json";
import thDashboard from "./locales/th-TH/dashboard.json";

export const messages = {
  "ja-JP": {
    common: jaCommon,
    navigation: jaNavigation,
    knowledge: jaKnowledge,
    interview: jaInterview,
    settings: jaSettings,
    errors: jaErrors,
    validation: jaValidation,
    dashboard: jaDashboard,
  },
  "en-US": {
    common: enCommon,
    navigation: enNavigation,
    knowledge: enKnowledge,
    interview: enInterview,
    settings: enSettings,
    errors: enErrors,
    validation: enValidation,
    dashboard: enDashboard,
  },
  "zh-CN": {
    common: zhCommon,
    navigation: zhNavigation,
    knowledge: zhKnowledge,
    interview: zhInterview,
    settings: zhSettings,
    errors: zhErrors,
    validation: zhValidation,
    dashboard: zhDashboard,
  },
  "th-TH": {
    common: thCommon,
    navigation: thNavigation,
    knowledge: thKnowledge,
    interview: thInterview,
    settings: thSettings,
    errors: thErrors,
    validation: thValidation,
    dashboard: thDashboard,
  },
} as const;
