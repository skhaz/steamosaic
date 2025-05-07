FROM public.ecr.aws/lambda/python:3.9

RUN yum install -y \
      mesa-libGL \
      mesa-libEGL \
      libSM \
      libXrender \
      libXext \
    && yum clean all

WORKDIR /var/task

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["main.handler"]
