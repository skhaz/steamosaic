FROM public.ecr.aws/lambda/python:3.13

# RUN microdnf install -y \
#       mesa-libGL \
#       mesa-libEGL \
#       libSM \
#       libXrender \
#       libXext \
#     && microdnf clean all

WORKDIR /var/task

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["main.handler"]
