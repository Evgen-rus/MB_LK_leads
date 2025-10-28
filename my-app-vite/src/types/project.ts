export type ProjectStatus = 'Активен' | 'На паузе';

export interface Project {
  id: number;
  name: string;
  type: string;
  globalLimit: number;
  dayLimit: number;
  status: ProjectStatus;
  ident: number;
}


