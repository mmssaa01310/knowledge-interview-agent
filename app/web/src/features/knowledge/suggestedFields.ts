import type { KnowledgeField } from "../../lib/api";

export const suggestedFields: KnowledgeField[] = [
  {
    name: "対象設備",
    description: "現象が発生した設備名やライン名",
    inputType: "short_text",
    required: true,
    askByAi: true,
    displayOrder: 1
  },
  {
    name: "発生条件",
    description: "朝一、段取り替え後、特定ワークなどの条件",
    inputType: "long_text",
    required: true,
    askByAi: true,
    displayOrder: 2
  },
  {
    name: "暫定対処",
    description: "現場で実施した一次対応",
    inputType: "long_text",
    required: false,
    askByAi: true,
    displayOrder: 3
  }
];
