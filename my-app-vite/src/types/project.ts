// Типы и интерфейсы данных проектов (страница «Проекты и каналы»)
export type ProjectStatus = 'Активен' | 'На паузе';

export interface Project {
  id: number;
  status: ProjectStatus;
  name: string;
  tag: string;
  type: string;
  dataLimit: number; // отображается как «Лимит»
  numbersToday: number; // «Номеров получено сегодня»
  numbersTotal: number; // «Номеров получено всего»
  daysReceived: string; // «Дни получения номеров», например: "Вт. Ср. Чт. Пт. Сб."
}


