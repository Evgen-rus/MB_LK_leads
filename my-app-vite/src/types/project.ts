// Типы и интерфейсы данных проектов (страница «Проекты и каналы»)
export type ProjectMutableStatus = 'Активен' | 'На паузе' | 'Удалён';
export type ProjectStatus = ProjectMutableStatus | 'Блокировка оператора';
export type DeliveryStatus = 'Активна' | 'На модерации' | 'Отключена';
export type CollectionSource =
  | 'Сайты'
  | 'Звонки'
  | 'СМС'
  | 'Ретросайты'
  | 'Ретрозвонки'
  | 'Пересечение';

export interface Project {
  id: number;
  status: ProjectStatus;
  deliveryStatus: DeliveryStatus; // «Статус отгрузки»: управляется нами
  name: string;
  tag: string;
  collectionSource: CollectionSource; // «Источник сбора»
  dataSourceCode: 'B1' | 'B2' | 'B3' | 'B4' | 'UNMAPPED';
  regionMode?: 'include' | 'exclude';
  regions?: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  dataLimit: number; // отображается как «Лимит»
  numbersToday: number; // «Номеров получено сегодня»
  numbersTotal: number; // «Номеров получено всего»
  numbersPeriod: number; // «Номеров за период» (выбранный в календаре)
  daysReceived: string; // «Дни получения номеров», например: "Вт. Ср. Чт. Пт. Сб."
  sourcesCount: number; // «Доменов/номеров»: сколько источников используем для сбора
  createdAt: string; // «Дата создания проекта», формат YYYY-MM-DD
}


