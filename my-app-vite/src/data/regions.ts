export type RegionOption = {
  code: string;
  name: string;
};

export const regions: RegionOption[] = [
  { code: '1', name: 'Республика Адыгея' },
  { code: '2', name: 'Республика Башкортостан' },
  { code: '4', name: 'Республика Алтай' },
  { code: '5', name: 'Республика Дагестан' },
  { code: '6', name: 'Республика Ингушетия' },
  { code: '7', name: 'Республика Кабардино-Балкарская' },
  { code: '8', name: 'Республика Калмыкия' },
  { code: '9', name: 'Республика Карачаево-Черкесская' },
  { code: '10', name: 'Республика Карелия' },
  { code: '11', name: 'Республика Коми' },
  { code: '12', name: 'Республика Марий Эл' },
  { code: '13', name: 'Республика Мордовия' },
  { code: '14', name: 'Республика Саха (Якутия)' },
  { code: '15', name: 'Республика Северная Осетия - Алания' },
  { code: '16', name: 'Республика Татарстан' },
  { code: '17', name: 'Республика Тыва' },
  { code: '18', name: 'Республика Удмуртская' },
  { code: '19', name: 'Республика Хакасия' },
  { code: '20', name: 'Чеченская Республика' },
  { code: '21', name: 'Чувашская Республика' },
  { code: '22', name: 'Алтайский край' },
  { code: '23', name: 'Краснодарский край' },
  { code: '24', name: 'Красноярский край' },
  { code: '25', name: 'Приморский край' },
  { code: '26', name: 'Ставропольский край' },
  { code: '27', name: 'Хабаровский край' },
  { code: '28', name: 'Амурская обл.' },
  { code: '29', name: 'Архангельская обл.' },
  { code: '30', name: 'Астраханская обл.' },
  { code: '31', name: 'Белгородская обл.' },
  { code: '32', name: 'Брянская обл.' },
  { code: '33', name: 'Владимирская обл.' },
  { code: '34', name: 'Волгоградская обл.' },
  { code: '35', name: 'Вологодская обл.' },
  { code: '36', name: 'Воронежская обл.' },
  { code: '37', name: 'Ивановская обл.' },
  { code: '38', name: 'Иркутская обл.' },
  { code: '39', name: 'Калининградская обл.' },
  { code: '40', name: 'Калужская обл.' },
  { code: '41', name: 'Камчатский край' },
  { code: '42', name: 'Кемеровская обл.' },
  { code: '43', name: 'Кировская обл.' },
  { code: '44', name: 'Костромская обл.' },
  { code: '45', name: 'Курганская обл.' },
  { code: '46', name: 'Курская обл.' },
  { code: '48', name: 'Липецкая обл.' },
  { code: '49', name: 'Магаданская обл.' },
  { code: '51', name: 'Мурманская обл.' },
  { code: '52', name: 'Нижегородская обл.' },
  { code: '53', name: 'Новгородская обл.' },
  { code: '54', name: 'Новосибирская обл.' },
  { code: '55', name: 'Омская обл.' },
  { code: '56', name: 'Оренбургская обл.' },
  { code: '57', name: 'Орловская обл.' },
  { code: '58', name: 'Пензенская обл.' },
  { code: '59', name: 'Пермский край' },
  { code: '60', name: 'Псковская обл.' },
  { code: '61', name: 'Ростовская обл.' },
  { code: '62', name: 'Рязанская обл.' },
  { code: '63', name: 'Самарская обл.' },
  { code: '64', name: 'Саратовская обл.' },
  { code: '65', name: 'Сахалинская обл.' },
  { code: '66', name: 'Свердловская обл.' },
  { code: '67', name: 'Смоленская обл.' },
  { code: '68', name: 'Тамбовская обл.' },
  { code: '69', name: 'Тверская обл.' },
  { code: '70', name: 'Томская обл.' },
  { code: '71', name: 'Тульская обл.' },
  { code: '72', name: 'Тюменская обл.' },
  { code: '73', name: 'Ульяновская обл.' },
  { code: '74', name: 'Челябинская обл.' },
  { code: '76', name: 'Ярославская обл.' },
  { code: '77', name: 'г. Москва' },
  { code: '78', name: 'г. Санкт-Петербург' },
  { code: '79', name: 'Еврейская автономная обл.' },
  { code: '86', name: 'Ханты-Мансийский АО - Югра' },
  { code: '87', name: 'Чукотский АО' },
];

export type FederalDistrict = {
  id: string;
  name: string;
  shortName: string;
  codes: string[];
};

// Только регионы из справочника Prostats выше. Официальные субъекты, которых нет в каталоге, в округа не входят.
export const federalDistricts: FederalDistrict[] = [
  {
    id: 'cfo',
    name: 'Центральный ФО',
    shortName: 'ЦФО',
    codes: ['31', '32', '33', '36', '37', '40', '44', '46', '48', '57', '62', '67', '68', '69', '71', '76', '77'],
  },
  {
    id: 'szfo',
    name: 'Северо-Западный ФО',
    shortName: 'СЗФО',
    codes: ['10', '11', '29', '35', '39', '51', '53', '60', '78'],
  },
  {
    id: 'ufo',
    name: 'Южный ФО',
    shortName: 'ЮФО',
    codes: ['1', '8', '23', '30', '34', '61'],
  },
  {
    id: 'skfo',
    name: 'Северо-Кавказский ФО',
    shortName: 'СКФО',
    codes: ['5', '6', '7', '9', '15', '20', '26'],
  },
  {
    id: 'pfo',
    name: 'Приволжский ФО',
    shortName: 'ПФО',
    codes: ['2', '12', '13', '16', '18', '21', '43', '52', '56', '58', '59', '63', '64', '73'],
  },
  {
    id: 'urfo',
    name: 'Уральский ФО',
    shortName: 'УрФО',
    codes: ['45', '66', '72', '74', '86'],
  },
  {
    id: 'sfo',
    name: 'Сибирский ФО',
    shortName: 'СФО',
    codes: ['4', '17', '19', '22', '24', '38', '42', '54', '55', '70'],
  },
  {
    id: 'dfo',
    name: 'Дальневосточный ФО',
    shortName: 'ДФО',
    codes: ['14', '25', '27', '28', '41', '49', '65', '79', '87'],
  },
];

const regionOptionByCode = new Map<string, RegionOption>(regions.map((r) => [r.code, r]));

export function regionsInDistrict(district: FederalDistrict): RegionOption[] {
  return district.codes
    .map((code) => regionOptionByCode.get(code))
    .filter((region): region is RegionOption => Boolean(region));
}

const regionNameByCode = new Map<string, string>(regions.map((r) => [r.code, r.name]));
const regionCodeByName = new Map<string, string>(regions.map((r) => [r.name, r.code]));

export function normalizeRegionValues(values?: string[] | null): string[] {
  if (!values || values.length === 0) return [];
  const out: string[] = [];
  const seen = new Set<string>();

  values.forEach((raw) => {
    const value = String(raw ?? '').trim();
    if (!value) return;
    const code = value.match(/^\d+$/) ? value : regionCodeByName.get(value);
    const normalized = code || value;
    if (seen.has(normalized)) return;
    seen.add(normalized);
    out.push(normalized);
  });
  return out;
}

export function regionLabelByCode(code: string): string {
  return regionNameByCode.get(code) || code;
}



