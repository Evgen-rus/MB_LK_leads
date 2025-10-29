// Типы и интерфейсы данных проектов (страница «Проекты и каналы»)
export type ProjectStatus = 'Активен' | 'На паузе';
export type DeliveryStatus = 'Активна' | 'На модерации' | 'Отключена';

export interface Project {
  id: number;
  status: ProjectStatus;
  deliveryStatus: DeliveryStatus; // «Статус отгрузки»: управляется нами
  name: string;
  tag: string;
  type: string;
  dataLimit: number; // отображается как «Лимит»
  numbersToday: number; // «Номеров получено сегодня»
  numbersTotal: number; // «Номеров получено всего»
  daysReceived: string; // «Дни получения номеров», например: "Вт. Ср. Чт. Пт. Сб."
  sourcesCount: number; // «Доменов/номеров»: сколько источников используем для сбора
  createdAt: string; // «Дата создания проекта», формат YYYY-MM-DD
}


